#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""超分辨率重建核心"""

import numpy as np
import cv2


def upscale_bilinear(img, scale=2):
    """
    双线性插值上采样（作为基础参考）

    参数:
        img: 输入图像 (H, W, 3)
        scale: 放大倍率

    返回:
        上采样图像 (H*scale, W*scale, 3)
    """
    h, w = img.shape[:2]
    return cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_LINEAR)


def reconstruct_sr(frames, shifts, scale=2, weights=None):
    """
    多帧超分辨率重建

    核心思想：每帧有亚像素位移，相当于在高分辨率网格上
    采样了不同的位置。把这些信息融合到 2x 网格上。

    参数:
        frames: 对齐后的帧列表, 每帧 (H, W, 3) float32
        shifts: 每帧的位移 (dx, dy)
        scale: 超分倍率
        weights: 每帧的融合权重

    返回:
        超分辨率图像 (H*scale, W*scale, 3) float32
    """
    n = len(frames)
    h, w = frames[0].shape[:2]
    sr_h, sr_w = h * scale, w * scale

    # 初始化累加器
    sr_sum = np.zeros((sr_h, sr_w, 3), dtype=np.float64)
    sr_weight = np.zeros((sr_h, sr_w), dtype=np.float64)

    for i, (frame, (dx, dy)) in enumerate(zip(frames, shifts)):
        print(f"\r  融合: {i+1}/{n}", end="", flush=True)

        # 亚像素位移映射到高分辨率网格
        sr_dx = dx * scale
        sr_dy = dy * scale

        # 在高分辨率网格上放置这一帧
        # 用双三次插值上采样
        sr_frame = cv2.resize(frame, (sr_w, sr_h), interpolation=cv2.INTER_CUBIC)

        # 应用亚像素位移修正
        M = np.float32([[1, 0, sr_dx], [0, 1, sr_dy]])
        sr_frame = cv2.warpAffine(
            sr_frame, M, (sr_w, sr_h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REFLECT_101
        )

        # 获取权重
        if weights is not None:
            w = weights[i]
            # 上采样权重到高分辨率
            w_sr = cv2.resize(w, (sr_w, sr_h), interpolation=cv2.INTER_LINEAR)
        else:
            w_sr = np.ones((sr_h, sr_w), dtype=np.float32)

        # 加权累加
        for c in range(3):
            sr_sum[:, :, c] += sr_frame[:, :, c] * w_sr
        sr_weight += w_sr

    print()

    # 归一化
    sr_weight = np.maximum(sr_weight, 1e-8)
    sr_result = sr_sum / sr_weight[:, :, np.newaxis]

    return sr_result.astype(np.float32)


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
