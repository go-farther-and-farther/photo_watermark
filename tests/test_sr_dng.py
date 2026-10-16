# -*- coding: utf-8 -*-
"""Linear DNG 母版输出测试（设计：docs/superpowers/specs/2026-10-16-linear-dng-design.md）

- 白平衡乘数 -> AsShotNeutral 换算（纯函数）
- sRGB 预览渲染（WB -> XYZ -> sRGB）
- write_linear_dng：exiftool 识别为 DNG、关键标签齐全、像素可无损读回
- 线性相机域解码 + 元数据提取（有真实 NEF 才跑）
- JPG 连拍组自动回退 TIF（无相机空间）
"""
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


class TestWriteLinearDng:
    def test_creates_valid_dng(self, tmp_path):
        et = _exiftool_or_skip()
        import subprocess
        arr = np.full((64, 48, 3), 30000, np.uint16)
        arr[:32] = 10000
        dng = tmp_path / "out_2x.dng"
        assert sr_process.write_linear_dng(
            dng, arr, Z6_MATRIX, [0.6337, 1.0, 0.6344],
            camera_model="NIKON Z 6", exiftool=et)

        # exiftool 识别为 DNG 且关键标签齐全；必须无压缩（Deflate 会被 LR 拒收）
        r = subprocess.run([et, '-FileType', '-DNGVersion', '-ColorMatrix1',
                            '-AsShotNeutral', '-WhiteLevel', '-PhotometricInterpretation',
                            '-Compression', str(dng)], capture_output=True, text=True)
        assert 'DNG' in r.stdout
        assert 'Linear Raw' in r.stdout
        assert '0.9943' in r.stdout
        assert 'As Shot Neutral' in r.stdout
        assert 'Uncompressed' in r.stdout  # Deflate 会被 Lightroom 拒收

        # 像素无损读回（数据通道，DNG 即 TIFF）
        back = tifffile.imread(str(dng))
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
        """实测比值：ColorSpace.raw 下 libraw 已烙入 WB 乘数，AsShotNeutral=两次解码比值"""
        n = sr_process.measure_as_shot_neutral(Z6_NEF)
        assert n is not None and len(n) == 3
        assert all(0.3 < v < 3.5 for v in n)
        # 暖色场景（ISO4000 室内）：B 通道补偿大于 R（实测约 [0.78, 1.0, 1.35]）
        assert n[2] > n[0]

    @pytest.mark.skipif(not Z6_NEF.exists(), reason="无真实 Z6 NEF 测试文件")
    def test_read_raw_camera_linear_is_linear(self):
        img = sr_process.read_raw_camera_linear(Z6_NEF)
        assert img.shape[2] == 3 and img.dtype == np.float32
        assert 0.0 <= img.min() and img.max() <= 1.0
        # 线性域未套 as-shot WB：暖光场景 R 通道原始值高于 B（与最终观感相反）
        med = np.median(img.reshape(-1, 3), axis=0)
        assert med[0] > med[2]


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
