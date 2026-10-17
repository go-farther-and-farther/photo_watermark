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
