# -*- coding: utf-8 -*-
"""月亮/行星（暗背景亮小主体）ROI 堆栈链路测试

覆盖：
- detect_bright_subject：暗背景 + 紧凑亮小目标识别，正常风景/大面积主体不误判
- register_roi：平移配准精度（0.05px 级）、旋转配准（三脚架重新取景的滚转）
- fuse_rois：scale 倍网格融合（单帧应与双三次放大一致）、光度归一生效
- select_lucky：糊帧剔除与权重排序
- estimate_psf_sigma：高斯模糊盘缘反推 σ
- subject_tonemap：黑背景不抬、主体中位锚定、天空保持黑
- render_srgb_preview 黑主导分支：月亮不被拉成白饼
"""
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

cv2 = pytest.importorskip("cv2")

import sr_moon  # noqa: E402
import sr_process  # noqa: E402


def _disk_image(size=256, cx=None, cy=None, r=40, peak=0.5, bg=0.0015, sigma=0.0,
                seed=0):
    cx = size / 2 if cx is None else cx
    cy = size / 2 if cy is None else cy
    yy, xx = np.mgrid[0:size, 0:size]
    img = np.full((size, size), bg, np.float32)
    m = ((xx - cx) ** 2 + (yy - cy) ** 2) <= r ** 2
    img[m] = peak
    if sigma > 0:
        img = cv2.GaussianBlur(img, (0, 0), sigma)
    rng = np.random.default_rng(seed)
    return np.clip(img + rng.normal(0, 2e-4, img.shape).astype(np.float32), 0, 1)


class TestDetectSubject:
    def test_finds_moon_like_disk(self):
        g = _disk_image(size=512, r=60)          # 直径 120 / 512 → 占 4.3% 面积
        d = sr_moon.detect_bright_subject(g)
        assert d is not None
        assert abs(d['cx'] - 256) < 2 and abs(d['cy'] - 256) < 2
        assert abs(d['r'] - 60) < 4
        assert d['frac'] < 0.06

    def test_rejects_normal_scene(self):
        # 整帧都有内容的风景（无暗背景）不应被判为主体
        rng = np.random.default_rng(3)
        g = cv2.GaussianBlur(rng.random((256, 256)).astype(np.float32), (0, 0), 2)
        assert sr_moon.detect_bright_subject(g) is None

    def test_rejects_huge_subject(self):
        # 主体占画面太大（>6%）：那是普通构图，不该走 ROI 裁剪
        g = _disk_image(size=256, r=110)
        assert sr_moon.detect_bright_subject(g) is None


class TestRegister:
    def test_translation_precision(self):
        rng = np.random.default_rng(0)
        a = cv2.GaussianBlur(rng.random((160, 160)).astype(np.float32), (0, 0), 3)
        a = (a - a.min()) / (a.max() - a.min())
        for tx, ty in [(3.0, -2.0), (0.37, 0.61), (-5.2, 4.8)]:
            b = cv2.warpAffine(a, np.float32([[1, 0, tx], [0, 1, ty]]), (160, 160),
                               flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
            M, rms, info = sr_moon.register_roi(a, b)
            assert abs(M[0, 2] - tx) < 0.06, f'x 误差 {M[0, 2]-tx:+.3f}'
            assert abs(M[1, 2] - ty) < 0.06, f'y 误差 {M[1, 2]-ty:+.3f}'

    def test_rotation_recovered(self):
        # 三脚架重新取景的滚转：纯平移吃不掉，必须被 ECC 欧氏精修捞回来
        a = cv2.resize(_disk_image(size=200, cx=100, cy=100, r=55, sigma=0.8),
                       (400, 400), interpolation=cv2.INTER_CUBIC)
        ang = 0.4
        Mtrue = cv2.getRotationMatrix2D((200, 200), ang, 1.0)
        b = cv2.warpAffine(a, Mtrue, (400, 400), flags=cv2.INTER_CUBIC,
                           borderMode=cv2.BORDER_REFLECT)
        M, rms, info = sr_moon.register_roi(a, b)
        assert info['rotated'], f'旋转未被采纳（{info["rot_deg"]:+.3f}°）'
        # rot_deg 是矩阵角，与画面滚转符号相反，比较幅度即可
        assert abs(abs(info['rot_deg']) - ang) < 0.1, f'旋转误差 {info["rot_deg"]:+.3f}°'
        # 采纳旋转后残差应明显低于纯平移（这是"验收制"的实际意义）
        assert rms <= info['rms_trans']

    def test_translation_only_when_rotation_disabled(self):
        a = _disk_image(size=200, sigma=0.5)
        b = _disk_image(size=200, cx=102, sigma=0.5)
        M, _, info = sr_moon.register_roi(a, b, allow_rotation=False)
        assert np.allclose(M[0, :2], [1.0, 0.0])
        assert not info['rotated']


class TestFuse:
    def test_single_frame_matches_upsample(self):
        rng = np.random.default_rng(1)
        a = cv2.GaussianBlur(rng.random((120, 120)).astype(np.float32), (0, 0), 2)
        a = np.repeat(((a - a.min()) / a.max())[..., None], 3, axis=2)
        out = sr_moon.fuse_rois([a], [(0.0, 0.0)], 2, (0, 0))
        ref = cv2.resize(a, (240, 240), interpolation=cv2.INTER_CUBIC)
        assert out.shape == (240, 240, 3)
        assert np.mean(np.abs(out[8:-8, 8:-8] - ref[8:-8, 8:-8])) < 1e-6

    def test_shift_is_compensated(self):
        rng = np.random.default_rng(2)
        a = cv2.GaussianBlur(rng.random((120, 120)).astype(np.float32), (0, 0), 2)
        a = ((a - a.min()) / a.max())
        b = cv2.warpAffine(a, np.float32([[1, 0, 2.0], [0, 1, -1.0]]), (120, 120),
                           flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
        M, _, _ = sr_moon.register_roi(a, b)
        out = sr_moon.fuse_rois([a, b], [(0.0, 0.0), M], 2, (0, 0))
        ref = cv2.resize(a, (240, 240), interpolation=cv2.INTER_CUBIC)
        # 配准误差 <0.05px，边缘除外
        assert np.mean(np.abs(out[16:-16, 16:-16, 0] - ref[16:-16, 16:-16])) < 3e-3

    def test_gain_applied(self):
        a = np.full((40, 40, 3), 0.25, np.float32)
        b = np.full((40, 40, 3), 0.50, np.float32)
        out = sr_moon.fuse_rois([a, b], [(0.0, 0.0), (0.0, 0.0)], 1, (0, 0),
                                gains=[1.0, 0.5])
        assert abs(float(out.mean()) - 0.25) < 1e-6


class TestSelectLucky:
    def test_picks_sharp_and_weights(self):
        edges = [3.7, 3.9, 3.5, 2.9, 3.3]
        keep, w = sr_moon.select_lucky(edges, min_keep=2, keep_ratio=1.2)
        assert 3 in keep                    # 最锐的一定在
        assert all(edges[i] <= max(edges) for i in keep)
        assert w[keep.index(3)] == 1.0      # 最锐权重 1
        assert len(keep) == len(w)

    def test_min_keep_respected(self):
        keep, _ = sr_moon.select_lucky([2.0, 2.9, 3.0, 3.1], min_keep=4, keep_ratio=1.01)
        assert len(keep) == 4


class TestPsfSigma:
    def test_recovers_gaussian_sigma(self):
        for s in (1.0, 1.8):
            g = _disk_image(size=400, r=100, sigma=s)
            sig, edge, q = sr_moon.estimate_psf_sigma(g, 200, 200, 100)
            # 10-90% 宽度 ≈ 2.563σ，允许 15% 误差（离散采样+限幅）
            assert abs(sig - s) < 0.15 * s, f'σ={sig:.2f} 期望 {s}'


class TestTonemap:
    def test_sky_stays_black_and_subject_anchored(self):
        g = np.stack([_disk_image(size=300, r=70, peak=0.4)] * 3, axis=2)
        out = sr_moon.subject_tonemap(g, 70, center=(150, 150))
        yy, xx = np.mgrid[0:300, 0:300]
        sky = ((xx - 150) ** 2 + (yy - 150) ** 2) > (1.3 * 70) ** 2
        disk = ((xx - 150) ** 2 + (yy - 150) ** 2) < (0.8 * 70) ** 2
        lum = out @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        assert lum[sky].max() < 0.10, '天空被抬起'
        assert abs(float(np.median(lum[disk])) - 0.5) < 0.05

    def test_highlight_not_clipped(self):
        # 亮纹比盘面亮 1.8 倍时，软肩应压到 <1 而不是整片白
        base = _disk_image(size=300, r=70, peak=0.35)
        yy, xx = np.mgrid[0:300, 0:300]
        hot = ((xx - 170) ** 2 + (yy - 140) ** 2) < 12 ** 2
        base[hot] = 0.63
        g = np.stack([base] * 3, axis=2)
        out = sr_moon.subject_tonemap(g, 70, center=(150, 150))
        assert float(out.max()) <= 1.0
        assert float(out[hot][:, 0].mean()) < 0.99


class TestDarkDominantPreview:
    """通用预览的黑主导分支：月亮不能被全帧分位拉伸拉成白饼"""

    def test_moon_not_blown_out(self):
        # 盘面有明暗结构（月海/高地），不是均匀圆盘
        g = _disk_image(size=600, r=90, peak=0.45)
        yy, xx = np.mgrid[0:600, 0:600]
        rr = np.sqrt((xx - 300) ** 2 + (yy - 300) ** 2) / 90.0
        g = np.where(rr <= 1.0, g * (0.45 + 0.55 * np.clip(rr, 0, 1)), g)
        rgb = np.stack([g] * 3, axis=2)
        eye = np.eye(3, dtype=np.float64)
        out = sr_process.render_srgb_preview(rgb, (eye, 0.0), stretch=True)
        o = out.astype(np.float32) / 255.0
        disk = ((xx - 300) ** 2 + (yy - 300) ** 2) < (0.75 * 90) ** 2
        sky = ((xx - 300) ** 2 + (yy - 300) ** 2) > (2.2 * 90) ** 2
        med = float(np.median(o[disk]))
        assert med < 0.92, f'月面仍被拉顶: 中位 {med:.3f}'
        assert float(np.median(o[sky])) < 0.12, '天空被抬灰'
        # 明暗结构必须还在（中心暗、边缘亮），不能被压成一片白
        inner = float(np.median(o[((xx - 300) ** 2 + (yy - 300) ** 2) < (0.3 * 90) ** 2]))
        assert med - inner > 0.05, '月面层次被拉平'

    def test_normal_scene_keeps_old_behaviour(self):
        # 普通亮场景仍走原来的 0.5/99.5 全帧拉伸（不改变既有观感）
        rng = np.random.default_rng(7)
        base = 0.2 + 0.5 * np.clip(cv2.GaussianBlur(
            rng.random((256, 256)).astype(np.float32), (0, 0), 8), 0, 1)
        img = np.repeat(base[..., None], 3, axis=2)
        eye = np.eye(3, dtype=np.float64)
        out = sr_process.render_srgb_preview(img, (eye, 0.0), stretch=True)
        o = out.astype(np.float32) / 255.0
        assert float(o.min()) < 0.02 and float(o.max()) > 0.95
