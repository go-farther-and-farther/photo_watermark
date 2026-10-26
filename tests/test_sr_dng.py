# -*- coding: utf-8 -*-
"""Linear DNG 母版输出测试（设计：docs/superpowers/specs/2026-10-16-linear-dng-design.md）

- 白平衡乘数 -> AsShotNeutral 换算（纯函数）
- sRGB 预览渲染（WB -> XYZ -> sRGB）
- write_linear_dng：Adobe 结构（IFD0 预览 + SubIFD LinearRaw 瓦片，单 IFD 裸结构
  会被 LR 拒收）、关键标签齐全、raw 像素可无损读回
- 线性相机域解码 + 元数据提取（有真实 NEF 才跑）
- JPG 连拍组自动回退 TIF（无相机空间）
"""
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

cv2 = pytest.importorskip("cv2")
tifffile = pytest.importorskip("tifffile")

import sr_process  # noqa: E402

# 真实 Z6 测试帧（Z6 用户的机器上有；CI/其他机器自动跳过）
Z6_NEF = Path(r'C:\Users\LQL\Pictures\raw\100NCZ_6\DSC_0020.NEF')

Z6_MATRIX = [[0.9943, -0.3269, -0.0839],
             [-0.5323, 1.3269, 0.2259],
             [-0.1198, 0.2083, 0.7557]]


def _exiftool_or_skip():
    from exif_reader import find_exiftool
    et = find_exiftool()
    if not et:
        pytest.skip("exiftool 不可用")
    return et


class TestNeutralFromCamMul:
    def test_rggb_cam_mul(self):
        # rawpy camera_whitebalance = [R, G, B, G2]，取前三通道取倒数
        neutral = sr_process.neutral_from_cam_mul([1.578125, 1.0, 1.576171875, 1.0])
        assert neutral == pytest.approx([0.6337, 1.0, 0.6344], abs=1e-3)

    def test_three_channel(self):
        assert sr_process.neutral_from_cam_mul([2.0, 1.0, 4.0]) == pytest.approx([0.5, 1.0, 0.25])


# 实测的 Z6「nocam 相机RGB -> 线性sRGB」仿射矩阵（libraw 回归得出，对角占优）
Z6_CAM_TO_SRGB = [[1.228, -0.395, -0.237],
                  [-0.102, 1.468, -0.450],
                  [0.017, -0.278, 1.667]]


class TestRenderSrgbPreview:
    def test_neutral_ramp_renders_channel_balanced(self):
        # 自洽检验：把"各通道相等"的灰阶渐变经 A^-1 逆映射回 nocam 域，
        # 渲染（A + gamma + 逐通道拉伸）后各通道应仍然相等（不偏色）
        A = np.array(Z6_CAM_TO_SRGB, np.float64)
        ramp = np.linspace(0.05, 0.95, 8 * 12).reshape(8, 12).astype(np.float64)
        target = np.stack([ramp] * 3, axis=2)                      # 线性sRGB 中性渐变
        Ainv = np.linalg.inv(A)
        nocam = target @ Ainv.T                                     # (b=0 时)逆映射
        out = sr_process.render_srgb_preview(nocam.astype(np.float32), (A, np.zeros(3)))
        assert out.shape == (8, 12, 3) and out.dtype == np.uint8
        assert out.max() > 0 and np.isfinite(out).all()
        diff = np.abs(out.astype(int) - out.astype(int)[:, :, :1]).max()
        assert diff < 12                                            # 三通道一致

    def test_output_in_uint8_range(self):
        rng = np.random.default_rng(3)
        linear = rng.random((16, 24, 3)).astype(np.float32)
        out = sr_process.render_srgb_preview(linear, (np.array(Z6_CAM_TO_SRGB), np.zeros(3)))
        assert out.min() >= 0 and out.max() <= 255


def _read_subifd_raw(dng_path):
    """手动解析 SubIFDs -> LinearRaw 瓦片并拼装（tifffile 不把 SubIFD 暴露为 series）"""
    tf = tifffile.TiffFile(str(dng_path))
    fh = tf.filehandle
    off = tf.pages[0].tags[0x014A].value[0]      # SubIFDs 首偏移
    fh.seek(off)
    n_entries = struct.unpack('<H', fh.read(2))[0]
    tags = {}
    base = off + 2
    for k in range(n_entries):
        fh.seek(base + k * 12)
        code, typ, cnt = struct.unpack('<HHI', fh.read(8))
        tags[code] = (typ, cnt, fh.read(4))

    def rd(code, n=None):
        typ, cnt, vo = tags[code]
        n = cnt if n is None else n
        esz, fmt = {3: (2, 'H'), 4: (4, 'I')}[typ]
        if esz * n <= 4:
            return struct.unpack('<' + fmt * n, vo[:esz * n])
        fh.seek(struct.unpack('<I', vo)[0])
        return struct.unpack('<' + fmt * n, fh.read(esz * n))

    w, h = rd(0x0100)[0], rd(0x0101)[0]
    tw_, tl = rd(0x0142)[0], rd(0x0143)[0]
    toff = rd(0x0144)
    tcnt = rd(0x0145)
    photo = rd(0x0106)[0]
    ny, nx = (h + tl - 1) // tl, (w + tw_ - 1) // tw_
    assert len(toff) == nx * ny
    out = np.empty((h, w, 3), np.uint16)
    for i in range(len(toff)):
        ty, tx = divmod(i, nx)
        th, tw = min(tl, h - ty * tl), min(tw_, w - tx * tw_)
        fh.seek(toff[i])
        tile = np.frombuffer(fh.read(tcnt[i]), '<u2', tl * tw_ * 3).reshape(tl, tw_, 3)
        out[ty*tl:ty*tl+th, tx*tw_:tx*tw_+tw] = tile[:th, :tw]  # 边缘瓦片按规范填充
    return photo, (tw_, tl), out


class TestWriteLinearDng:
    def test_preview_resized_to_max_side(self, tmp_path):
        """内嵌预览按 preview_max_side 缩放（默认 4096）：Windows 资源管理器/
        照片应用显示的就是这层，太小会被当成"尺寸大画质低"（实测 1600 时
        预览只有 1024x749）。JPEG 压缩保持 photometric=2（Adobe 惯例）。"""
        et = _exiftool_or_skip()
        arr = np.full((64, 48, 3), 30000, np.uint16)
        preview = np.zeros((375, 500, 3), np.uint8)
        dng = tmp_path / "prev.dng"
        assert sr_process.write_linear_dng(
            dng, arr, Z6_MATRIX, [0.6337, 1.0, 0.6344],
            camera_model="NIKON Z 6", exiftool=et, preview_rgb=preview,
            preview_max_side=120)
        tf = tifffile.TiffFile(str(dng))
        ifd0 = tf.pages[0]
        assert ifd0.shape == (90, 120, 3)       # 500x375 -> 120x90
        assert ifd0.photometric == 2            # JPEG 不得写成 YCbCr(6)
        back = ifd0.asarray()
        assert back.shape == (90, 120, 3)       # JPEG 预览可解码

    def test_creates_valid_dng(self, tmp_path):
        et = _exiftool_or_skip()
        import subprocess
        arr = np.full((64, 48, 3), 30000, np.uint16)
        arr[:32] = 10000
        preview = np.zeros((16, 12, 3), np.uint8)
        preview[8:] = 255
        dng = tmp_path / "out_2x.dng"
        assert sr_process.write_linear_dng(
            dng, arr, Z6_MATRIX, [0.6337, 1.0, 0.6344],
            camera_model="NIKON Z 6", exiftool=et, preview_rgb=preview,
            orig_raw_name="DSC_0001.NEF")

        # exiftool 识别为 DNG 且关键标签齐全；必须无压缩（Deflate 会被 LR 拒收）
        r = subprocess.run([et, '-FileType', '-DNGVersion', '-ColorMatrix1',
                            '-AsShotNeutral', '-PhotometricInterpretation',
                            '-Compression', str(dng)],
                           capture_output=True, text=True)
        assert 'DNG' in r.stdout
        assert 'Linear Raw' in r.stdout
        assert '0.9943' in r.stdout
        assert 'As Shot Neutral' in r.stdout
        assert 'Uncompressed' in r.stdout  # Deflate 会被 Lightroom 拒收

        # Adobe 结构：IFD0=RGB 预览(NewSubfileType=1)，SubIFD=LinearRaw 瓦片(NewSubfileType=0)
        tf = tifffile.TiffFile(str(dng))
        ifd0 = tf.pages[0]
        assert ifd0.photometric == 2 and ifd0.tags[0x00FE].value == 1
        assert ifd0.shape == (16, 12, 3)
        # Orientation 必须是 1（print 转换曾把 -Orientation=1 写成 3）；
        # CalibrationIlluminant1 必须是 21=D65（全局 -n 曾把它写成 0xD65=3429）
        assert ifd0.tags[0x0112].value == 1
        assert ifd0.tags[0xC65A].value == 21
        # IFD0 带 DNGVersion（缺失会被 LR 整体拒收）
        assert ifd0.tags[0xC612].value == b'\x01\x04\x00\x00'
        photo, (tw_, tl_), raw = _read_subifd_raw(dng)
        assert photo == 34892
        assert tw_ == tl_ == 64            # 48x64 小图 -> 单块 16 倍数瓦片
        np.testing.assert_array_equal(raw, arr)


class TestJpegXlDng:
    def test_lossless_jxl_dng_roundtrip(self, tmp_path):
        """DNG 1.7 + JPEG XL 无损：体积远小于无压缩，像素可逐位读回。"""
        et = _exiftool_or_skip()
        import subprocess
        rng = np.random.default_rng(5)
        arr = (rng.random((96, 64, 3)) * 65535).astype(np.uint16)
        dng = tmp_path / "jxl_1.5x.dng"
        assert sr_process.write_linear_dng(
            dng, arr, Z6_MATRIX, [0.6337, 1.0, 0.6344],
            camera_model="NIKON Z 6", exiftool=et,
            compression='jpegxl_dng', dng_version='1.7.0.0')

        r = subprocess.run([et, '-s3', '-DNGVersion', '-Compression', str(dng)],
                           capture_output=True, text=True)
        assert '1.7.0.0' in r.stdout
        assert 'JPEG XL' in r.stdout
        tf = tifffile.TiffFile(str(dng))
        assert tf.pages[0].tags[0xC612].value == b'\x01\x07\x00\x00'
        back = sr_process.load_subifd_rgb(dng)
        np.testing.assert_array_equal(back, arr)


class TestDeriveCameraToSrgb:
    @pytest.mark.skipif(not Z6_NEF.exists(), reason="无真实 Z6 NEF 测试文件")
    def test_affine_from_real_nef(self):
        r = sr_process.derive_camera_to_srgb(Z6_NEF)
        assert r is not None
        A, b = r
        assert A.shape == (3, 3) and b.shape == (3,)
        assert np.isfinite(A).all() and np.isfinite(b).all()
        diag = np.diag(A)
        assert (diag > 0.5).all() and (diag < 3).all()      # 对角占优且量级合理
        assert np.abs(b).max() < 0.01                        # 截距很小但非零（黑电平差）
        # 与实测参考（libraw 半尺寸仿射回归值）同数量级
        assert abs(A[0, 0] - 1.228) < 0.3


class TestRealNefMetadata:
    """有真实 Z6 NEF 才执行的线性域解码/元数据测试"""

    @pytest.mark.skipif(not Z6_NEF.exists(), reason="无真实 Z6 NEF 测试文件")
    def test_extract_dng_metadata(self):
        meta = sr_process.extract_dng_metadata(Z6_NEF)
        assert meta is not None
        assert meta['model'] == 'NIKON Z 6'
        m = np.array(meta['matrix'], np.float64)
        assert m.shape == (3, 3) and np.isfinite(m).all()
        assert abs(m[0, 0] - 0.9943) < 0.01  # 与 dcraw 公开矩阵一致

    @pytest.mark.skipif(not Z6_NEF.exists(), reason="无真实 Z6 NEF 测试文件")
    def test_measure_as_shot_neutral(self):
        """实测比值 = AsShotNeutral（nocam 已除回 daylight，原生域）：≈ 1/cam_mul

        Z6 该帧 cam_mul=[1.578, 1, 1.576]（R≈B）-> AsShotNeutral≈[0.634, 1, 0.634]，
        LR as-shot 色温落滑块中段（此前数据烙 daylight 时标签被顶到 2250K 贴下限）。
        """
        n = sr_process.measure_as_shot_neutral(Z6_NEF)
        assert n is not None and len(n) == 3
        assert all(0.3 < v < 2.0 for v in n)
        assert n[0] == pytest.approx(n[2], abs=0.06)   # 相机 as-shot R≈B
        assert n[0] == pytest.approx(0.634, abs=0.08)
        # 与元数据换算 1/cam_mul 交叉验证（libraw 烙印 undo 的 B 通道残差 ~4%，放宽）
        import rawpy
        with rawpy.imread(str(Z6_NEF)) as raw:
            ref = sr_process.neutral_from_cam_mul(raw.camera_whitebalance)
        assert n[0] == pytest.approx(ref[0], rel=0.06)
        assert n[2] == pytest.approx(ref[2], rel=0.06)

    @pytest.mark.skipif(not Z6_NEF.exists(), reason="无真实 Z6 NEF 测试文件")
    def test_read_raw_camera_linear_is_linear(self):
        img = sr_process.read_raw_camera_linear(Z6_NEF)
        assert img.shape[2] == 3 and img.dtype == np.float32
        assert 0.0 <= img.min() and img.max() <= 1.0
        # 线性域未套 as-shot WB：暖光场景 R 通道原始值高于 B（与最终观感相反）
        med = np.median(img.reshape(-1, 3), axis=0)
        assert med[0] > med[2]


class TestMasterOutputSize:
    """1.5x 母版：2x 融合后按线性光降采样到 6×9 档"""

    def test_15x_size(self):
        assert sr_process._master_output_size(8256, 5504, 2, 1.5) == (6192, 4128, 1.5)

    def test_no_shrink_when_master_absent_or_equal(self):
        assert sr_process._master_output_size(100, 80, 2, None) == (100, 80, 2.0)
        assert sr_process._master_output_size(100, 80, 2, 2) == (100, 80, 2.0)


class TestDngMasterScale:
    def test_dng_master_is_15x_and_jpg_stays_2x(self, tmp_path, monkeypatch):
        """DNG 模式也必须遵守 master_scale：母版 1.5x，JPG 仍 2x。

        用 JPG 连拍模拟 RAW 组（把 .jpg 临时算作 RAW 扩展名），
        只验证输出分支/尺寸/命名，不真写 DNG。
        """
        sys.path.insert(0, str(PROJECT_ROOT / 'tests'))
        from test_sr import _base_image, _warp

        img = _base_image()
        files = []
        for i, (dx, dy) in enumerate([(0, 0), (0.35, 0.6), (0.7, 0.1),
                                      (-0.3, 0.45), (0.2, -0.35)]):
            frame = (np.clip(_warp(img, dx, dy), 0, 1) * 255).astype(np.uint8)
            bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            p = tmp_path / f'f_{i}.jpg'
            p.write_bytes(cv2.imencode('.jpg', bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes())
            files.append(p)

        captured = {}

        def fake_write(path, arr, *a, **k):
            captured['shape'] = arr.shape
            Path(path).write_bytes(b'x')
            return True

        monkeypatch.setattr(sr_process, 'RAW_EXTS', sr_process.RAW_EXTS + ('.jpg',))
        monkeypatch.setattr('exif_reader.find_exiftool', lambda: 'exiftool')
        monkeypatch.setattr(sr_process, 'extract_dng_metadata',
                            lambda p: {'matrix': Z6_MATRIX, 'model': 'NIKON Z 6'})
        monkeypatch.setattr(sr_process, 'measure_as_shot_neutral',
                            lambda p: [0.6337, 1.0, 0.6344])
        monkeypatch.setattr(sr_process, 'derive_camera_to_srgb',
                            lambda p: (np.array(Z6_CAM_TO_SRGB), np.zeros(3)))
        monkeypatch.setattr(sr_process, 'copy_exif_from_frame', lambda *a, **k: False)
        monkeypatch.setattr(sr_process, 'write_linear_dng', fake_write)

        sr_process.process(files=list(files), output_path=str(tmp_path / 'r.tif'),
                           scale=2, max_frames=5, master_scale=1.5, output_dng=True,
                           use_subject_roi=False, use_homography=False,
                           use_local_align=False, use_noise_weight=False,
                           rl_sharpen=False, sharpen=0.0, blur_gate=False, sharp_gate=False)

        assert (tmp_path / 'r_1.5x.dng').exists()
        assert not (tmp_path / 'r_2x.dng').exists()
        assert (tmp_path / 'r_2x.jpg').exists()
        jpg = cv2.imread(str(tmp_path / 'r_2x.jpg'))
        assert jpg is not None
        mh, mw = captured['shape'][:2]
        # 2x 画布 -> 1.5x（0.75），允许逐边 1px 取整
        assert abs(mw - round(jpg.shape[1] * 0.75)) <= 1
        assert abs(mh - round(jpg.shape[0] * 0.75)) <= 1


class TestJpgFallback:
    def test_jpg_burst_falls_back_to_tif(self, tmp_path):
        """JPG 连拍没有相机空间：即使要求 DNG 也回退 TIF，不产出 .dng"""
        sys.path.insert(0, str(PROJECT_ROOT / 'tests'))
        from test_sr import _base_image, _warp
        img = _base_image()
        for i, (dx, dy) in enumerate([(0, 0), (0.35, 0.6), (0.7, 0.1), (1.2, 1.5), (-0.5, 0.8), (0.2, -0.7)]):
            frame = (np.clip(_warp(img, dx, dy), 0, 1) * 255).astype(np.uint8)
            bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            ok, buf = cv2.imencode('.jpg', bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
            (tmp_path / f"f_{i}.jpg").write_bytes(buf.tobytes())

        sr_process.process(str(tmp_path), str(tmp_path / 'r.tif'),
                           scale=2, max_frames=6, use_deghost=True, sharpen=0.0,
                           output_dng=True)
        assert (tmp_path / 'r.tif').exists()
        assert not list(tmp_path.glob('*.dng'))
