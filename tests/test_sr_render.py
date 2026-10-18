# -*- coding: utf-8 -*-
"""预览渲染测试：高光溢出去紫/去粉（向中性白收敛）、色度降噪

背景：通道裁剪差异让过曝区在预览里偏紫/偏粉（LR 有高光修复所以正常）；
融合降噪后色彩噪斑仍可见——色度通道降采样处理即可压掉，成本忽略。
"""
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

cv2 = pytest.importorskip("cv2")

import sr_process  # noqa: E402


class TestHighlightDesat:
    """高光去色：mx>0.88 的像素向 (mx,mx,mx) 收敛，亮度不变、色度归零"""

    def _render(self, srgb_linear):
        img = (np.clip(srgb_linear, 0, 1) * 65535).astype(np.uint16)
        eye = np.eye(3, dtype=np.float64)
        return sr_process.render_srgb_preview(img.astype(np.float32) / 65535.0, (eye, 0.0),
                                              stretch=False).astype(np.float32) / 255.0

    def test_clipped_magenta_goes_neutral(self):
        # 洋红溢出 (1, 0.6, 0.8)：修复后应接近中性白，而不是紫/粉
        lin = np.array([[[1.0, 0.60, 0.80],
                         [0.30, 0.28, 0.29]]], dtype=np.float32)
        out = self._render(lin)
        hi = out[0, 0]
        assert hi.max() - hi.min() < 0.06, f'高光仍有色偏: {hi}'
        assert hi.mean() > 0.85, f'高光亮度被压暗: {hi}'

    def test_normal_colors_untouched(self):
        # 中低亮度的均匀色块必须原样（高光去色不波及、色度降噪对均匀区保持）
        lin = np.full((8, 8, 3), [0.30, 0.10, 0.10], dtype=np.float32)
        out = self._render(lin)
        rg = out[:, :, 0].mean() - out[:, :, 1].mean()
        assert rg > 0.15, f'红色色度被压掉: R-G={rg:.3f}'


class TestHighlightHeadroom:
    """白天大天空：全帧 P99.5 落在天空里，直接定白点会把天空压成一片纯白
    （2026-10-18 实测：24mm 白天组 JPG 天空 73% 像素 ≥250，DNG 里峰值只有 0.90）"""

    def _render(self, lin, stretch=True):
        eye = np.eye(3, dtype=np.float64)
        return sr_process.render_srgb_preview(
            np.clip(lin, 0, 1).astype(np.float32), (eye, 0.0),
            stretch=stretch).astype(np.float32) / 255.0

    def test_bright_sky_keeps_detail(self):
        # 下半是暗前景，上半是大片亮天空（带云层起伏）
        h, w = 240, 320
        yy, xx = np.mgrid[0:h, 0:w]
        lin = np.full((h, w, 3), 0.08, np.float32)          # 前景
        sky = 0.55 + 0.05 * np.sin(xx / 12.0) + 0.02 * np.cos(yy / 7.0)
        lin[yy < h // 2] = np.repeat(sky[..., None], 3, axis=2)[yy < h // 2]
        out = self._render(lin)
        top = out[:h // 2].mean(axis=2)
        bright = float((top >= 0.98).mean())
        assert bright < 0.05, f'天空 {bright:.0%} 像素被顶白'
        assert float(top.std()) > 0.01, '天空层次被压平'
        assert float(top.max()) < 0.995

    def test_night_scene_unchanged(self):
        # 夜景（暗背景 + 少量亮灯）：仍走原来的拉伸——灯被拉到接近白，
        # 背景压到黑，也就是"暗场提亮"的旧观感没有被本次改动影响
        h, w = 200, 300
        lin = np.full((h, w, 3), 0.02, np.float32)
        lin[80:110, 100:160] = 0.6
        out = self._render(lin)
        patch = out[85:105, 110:150].mean()
        bg = out[0:60, 0:80].mean()
        assert float(patch) > 0.85, f'亮灯没有被拉亮: {float(patch):.2f}'
        assert float(bg) < 0.05, f'暗背景被抬灰: {float(bg):.2f}'


class TestChromaDenoise:
    """色度降噪：色彩噪斑应被压掉，亮度结构（黑白边缘）保持"""

    def test_color_speckle_suppressed(self):
        rng = np.random.default_rng(5)
        base = np.full((64, 64, 3), 0.5, dtype=np.float32)
        noise = rng.normal(0, 0.10, (64, 64, 3)).astype(np.float32)
        noisy = np.clip(base + noise, 0, 1)
        out = sr_process._chroma_denoise(noisy)
        # 色度能量（各通道与亮度之差）应显著下降
        def chroma_energy(img):
            y = img.mean(axis=2)
            return float(np.mean((img - y[..., None]) ** 2))
        assert chroma_energy(out) < chroma_energy(noisy) * 0.4

    def test_luma_edge_kept(self):
        # 黑白竖条纹（纯亮度结构）不应被模糊
        x = np.arange(64)
        edge = np.where((x // 4) % 2 == 0, 0.2, 0.8).astype(np.float32)
        img = np.stack([edge] * 64, axis=0)[..., None] * np.ones((1, 1, 3), np.float32)
        out = sr_process._chroma_denoise(img)
        col = out[32, :, 0]
        assert col.max() - col.min() > 0.5, '亮度边缘被抹掉'
