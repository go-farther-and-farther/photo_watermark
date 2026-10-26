# -*- coding: utf-8 -*-
"""多帧超分（super_res 系列）集成测试

用合成 JPG 连拍（已知亚像素位移）验证：
1) align.estimate_shift 的位移估计精度（warp 平移 t → 估计 ≈ t）
2) sr_process.process 端到端：输出 JPG/TIFF 存在、尺寸约 2 倍、非平凡内容

RAW 分支（rawpy）与连拍分组（burst_tools，需真实 RAW 的 EXIF 时间）不做单测。
"""
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

cv2 = pytest.importorskip("cv2")
tifffile = pytest.importorskip("tifffile")

from align import estimate_shift  # noqa: E402
import sr_process  # noqa: E402


def _find_exiftool_or_skip():
    from exif_reader import find_exiftool
    et = find_exiftool()
    if not et:
        pytest.skip("exiftool 不可用")
    return et


def _base_image(w=320, h=240, seed=7):
    """高密集纹理的合成图：相位相关需要丰富的高频信息"""
    rng = np.random.default_rng(seed)
    x, y = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    base = 0.5 + 0.25 * np.sin(x * 0.31 + y * 0.17) + 0.15 * np.sin(x * 0.83 - y * 0.51)
    base += rng.normal(0, 0.02, base.shape)
    img = np.stack([np.clip(base, 0.05, 0.95)] * 3, axis=2).astype(np.float32)
    return img


def _warp(img, dx, dy):
    h, w = img.shape[:2]
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REFLECT_101)


class TestEstimateShift:
    def test_subpixel_shift_recovered(self):
        img = _base_image()
        shifted = _warp(img, 1.7, -0.9)
        dx, dy, resp = estimate_shift(img[:, :, 0], shifted[:, :, 0])
        assert abs(dx - 1.7) < 0.25
        assert abs(dy + 0.9) < 0.25
        assert resp > 0.5

    def test_zero_shift(self):
        img = _base_image()
        dx, dy, resp = estimate_shift(img[:, :, 0], img[:, :, 0])
        assert abs(dx) < 0.25 and abs(dy) < 0.25


class TestProcess:
    def test_jpg_burst_end_to_end(self, tmp_path):
        burst = tmp_path / "burst"
        burst.mkdir()
        shifts = [(0.0, 0.0), (0.35, 0.6), (0.7, 0.1), (1.2, 1.5), (-0.5, 0.8), (0.2, -0.7)]
        img = _base_image()
        for i, (dx, dy) in enumerate(shifts):
            frame = (np.clip(_warp(img, dx, dy), 0, 1) * 255).astype(np.uint8)
            bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            ok, buf = cv2.imencode('.jpg', bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
            assert ok
            (burst / f"frame_{i:02d}.jpg").write_bytes(buf.tobytes())

        out_base = tmp_path / "result"
        sr_process.process(
            str(burst), str(out_base) + '.tif',
            scale=2, max_frames=6, max_shift=50.0,
            use_deghost=True, sharpen=0.0, use_denoise=False,
        )

        # 全尺寸 TIF：存在、约 2 倍尺寸（重叠区裁掉位移跨距）、3 通道
        tif = tmp_path / "result.tif"
        assert tif.exists()
        arr = tifffile.imread(str(tif))
        h, w = img.shape[:2]
        out_h, out_w = arr.shape[:2]
        assert arr.ndim == 3 and arr.shape[2] == 3
        assert 2 * h - 12 <= out_h <= 2 * h
        assert 2 * w - 12 <= out_w <= 2 * w
        # 非平凡内容：不是全黑/全白
        assert float(arr.std()) > 1000.0

        # JPG 预览：存在、可解码、与 TIF 同尺寸
        jpg = tmp_path / "result_2x.jpg"
        assert jpg.exists()
        jimg = cv2.imread(str(jpg))
        assert jimg is not None
        assert (jimg.shape[0], jimg.shape[1]) == (out_h, out_w)

    def test_compare_out_generates_two_panel_png(self, tmp_path):
        """compare_out=True：成品之外自动出双图对比（中位锐度单帧 vs 合成）"""
        pytest.importorskip("PIL")
        burst = tmp_path / "burst2"
        burst.mkdir()
        img = _base_image()
        for i, (dx, dy) in enumerate([(0.0, 0.0), (0.4, 0.2), (0.9, 0.7),
                                      (0.1, 1.1), (-0.4, 0.3)]):
            frame = (np.clip(_warp(img, dx, dy), 0, 1) * 255).astype(np.uint8)
            bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            ok, buf = cv2.imencode('.jpg', bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
            assert ok
            (burst / f"f{i}.jpg").write_bytes(buf.tobytes())

        out_base = tmp_path / "cmp"
        sr_process.process(
            str(burst), str(out_base) + '.tif',
            scale=2, max_frames=5, max_shift=50.0, sharpen=0.0,
            compare_out=True, compare_long_side=300,
        )
        cmp_png = tmp_path / "cmp_2x_对比.png"     # 不在 SR输出 结构下 → 与 JPG 同目录
        assert cmp_png.exists(), f'对比图未生成: {list(tmp_path.iterdir())}'
        jimg = cv2.imread(str(tmp_path / "cmp_2x.jpg"))
        assert jimg is not None
        out_h, out_w = jimg.shape[:2]
        cw = min(300, out_w)
        ch = round(out_h * cw / out_w)
        from PIL import Image
        with Image.open(cmp_png) as im:
            assert im.size == (cw * 2 + 18 * 3, 58 + 34 + ch + 44 + 18)


class TestExifCopy:
    """参考帧完整 EXIF（含镜头型号）经 ExifTool 拷到输出文件"""

    def _make_source(self, path):
        """带镜头/快门/ISO 的源帧（模拟参考帧）"""
        import piexif
        frame = (np.clip(_base_image(), 0, 1) * 255).astype(np.uint8)
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        ok, buf = cv2.imencode('.jpg', bgr)
        assert ok
        Path(path).write_bytes(buf.tobytes())
        exif = piexif.dump({
            "0th": {piexif.ImageIFD.Make: b"Nikon", piexif.ImageIFD.Model: b"NIKON Z 6"},
            "Exif": {
                piexif.ExifIFD.DateTimeOriginal: b"2026:10:16 09:00:00",
                piexif.ExifIFD.ExposureTime: (1, 250),
                piexif.ExifIFD.ISOSpeedRatings: 3600,
                piexif.ExifIFD.FNumber: (28, 10),
                piexif.ExifIFD.FocalLength: (5000, 100),
                piexif.ExifIFD.LensMake: b"NIKON",
                piexif.ExifIFD.LensModel: b"NIKKOR Z 50mm f/1.8 S",
            },
        })
        piexif.insert(exif, str(path))

    def test_copy_full_exif_with_output_dims(self, tmp_path):
        et = _find_exiftool_or_skip()
        src = tmp_path / "ref.jpg"
        self._make_source(src)
        out = tmp_path / "result_2x.jpg"
        frame = np.full((120, 160, 3), 200, np.uint8)
        ok, buf = cv2.imencode('.jpg', frame)
        Path(out).write_bytes(buf.tobytes())

        assert sr_process.copy_exif_from_frame(et, src, out, width=160, height=120)

        import exifread
        with open(out, 'rb') as f:
            tags = exifread.process_file(f, details=False)
        assert 'NIKKOR Z 50mm' in str(tags.get('EXIF LensModel', ''))
        assert str(tags.get('EXIF ISOSpeedRatings', '')) == '3600'
        assert 'EXIF ExposureTime' in tags
        # 尺寸标签改写为输出实际值，而不是源帧的（exifread 键名：Width/Length）
        assert int(str(tags.get('EXIF ExifImageWidth', '0'))) == 160
        assert int(str(tags.get('EXIF ExifImageLength', '0'))) == 120

    def test_copy_to_tiff(self, tmp_path):
        et = _find_exiftool_or_skip()
        src = tmp_path / "ref.jpg"
        self._make_source(src)
        out = tmp_path / "result.tif"
        tifffile.imwrite(str(out), np.zeros((120, 160, 3), dtype=np.uint16))

        assert sr_process.copy_exif_from_frame(et, src, out, width=160, height=120)

        import exifread
        with open(out, 'rb') as f:
            tags = exifread.process_file(f, details=False)
        assert 'NIKKOR Z 50mm' in str(tags.get('EXIF LensModel', ''))
