#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合成后处理：锐化与降噪（供 sr_process 的非 DNG 输出路径使用）

历史上的重建核心（upscale_bilinear/reconstruct_sr）已被 sr_process.reconstruct
完全替代并删除（2026-10-26 清扫：全仓库零调用）。
"""

import numpy as np
import cv2


def enhance_sharpness(img, strength=0.5):
    """
    多尺度锐化增强

    参数:
        img: 输入图像 (H, W, 3) float32
        strength: 锐化强度 0-1

    返回:
        锐化后的图像
    """
    # 小尺度细节 + 中尺度边缘
    blur1 = cv2.GaussianBlur(img, (0, 0), 1.0)
    blur2 = cv2.GaussianBlur(img, (0, 0), 3.0)

    detail = img - blur1       # 高频细节
    edge = blur1 - blur2       # 中频边缘

    sharpened = img + strength * detail + strength * 0.5 * edge
    return np.clip(sharpened, 0, 1).astype(np.float32)


def denoise(img, strength=5):
    """
    降噪

    参数:
        img: 输入图像 (H, W, 3) float32
        strength: 降噪强度

    返回:
        降噪后的图像
    """
    img_uint8 = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    denoised = cv2.fastNlMeansDenoisingColored(img_uint8, None, strength, strength, 7, 21)
    return denoised.astype(np.float32) / 255.0
