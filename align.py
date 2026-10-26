#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""帧间对齐：中心裁切 + 相位相关"""

import numpy as np
import cv2


def read_raw_as_gray(raw_path, half_size=False):
    """读取 RAW 或 JPG 并转灰度（带 gamma）"""
    path = str(raw_path).lower()
    if path.endswith(('.jpg', '.jpeg', '.png', '.tif', '.tiff')):
        # 普通图片直接读取。cv2.imread 在 Windows 下不认中文路径（静默返回
        # None），一律走字节流 imdecode——用户照片目录带中文是常态
        img = cv2.imdecode(np.fromfile(str(raw_path), dtype=np.uint8),
                           cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(f"无法读取: {raw_path}")
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        if half_size:
            gray = cv2.resize(gray, (gray.shape[1]//2, gray.shape[0]//2))
    else:
        # RAW 文件
        import rawpy
        raw = rawpy.imread(str(raw_path))
        rgb = raw.postprocess(
            half_size=half_size,
            use_camera_wb=True,
            use_auto_wb=False,
            output_bps=16,
            output_color=rawpy.ColorSpace.sRGB,
            no_auto_bright=True,
            gamma=(1, 1),
        )
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) / 65535.0
    # gamma 校正让暗部可见
    gray = np.power(np.clip(gray, 0, 1), 1/2.2)
    return gray


def sharpness_score(gray):
    """单帧锐度：3x3 拉普拉斯方差（越大越锐）。

    输入应是 read_raw_as_gray(half_size=True) 的半尺寸灰度：rawpy 的
    half_size 解码已按 2x2 合并像素，噪声被天然压掉，直接求梯度即可——
    若再降采样到 1/4 分辨率，几像素的运动模糊会被抹平（实测 46 帧防抖
    A/B 组：与全分辨率真值的排序一致性 0.840 vs 0.997，门控决策一致率
    27% vs 97%）。无参考帧依赖，单帧即可判定；得分只在同一解码尺度下
    可比，门控按组内相对比例取阈，绝对值无意义。
    """
    return float(cv2.Laplacian(gray.astype(np.float64), cv2.CV_64F, ksize=3).var())


def estimate_shift(ref_gray, target_gray, crop_ratio=0.6):
    """
    中心裁切 + 相位相关估计位移

    参数:
        ref_gray: 参考帧灰度图
        target_gray: 目标帧灰度图
        crop_ratio: 中心裁切比例 (0.6 = 只用中间60%区域)

    返回:
        (dx, dy, response) 亚像素位移与相位相关响应值（0..1，越高越可信）
    """
    h, w = ref_gray.shape

    # 中心裁切
    ch = int(h * crop_ratio)
    cw = int(w * crop_ratio)
    y0 = (h - ch) // 2
    x0 = (w - cw) // 2

    ref_crop = ref_gray[y0:y0+ch, x0:x0+cw]
    tgt_crop = target_gray[y0:y0+ch, x0:x0+cw]

    # 相位相关
    win = cv2.createHanningWindow((cw, ch), cv2.CV_32F)
    shift, response = cv2.phaseCorrelate(ref_crop * win, tgt_crop * win)

    dx, dy = shift
    return dx, dy, response


def align_frame(ref_img, target_img, dx, dy):
    """用位移对齐目标帧"""
    h, w = ref_img.shape[:2]
    M = np.float32([[1, 0, -dx], [0, 1, -dy]])
    aligned = cv2.warpAffine(
        target_img, M, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REFLECT_101
    )
    return aligned


def find_best_ref_frame(raw_files, half_size=True):
    """
    找到最稳定的参考帧（位移最接近中位数的帧）

    策略：用中间帧预对齐，选位移最接近中位数的帧作为最终参考帧
    """
    n = len(raw_files)
    temp_ref = n // 2  # 临时用中间帧

    print(f"  预对齐（找最佳参考帧）...")
    temp_ref_gray = read_raw_as_gray(raw_files[temp_ref], half_size=half_size)

    # 算每帧相对于中间帧的位移
    shifts = [(0.0, 0.0)] * n
    for i, f in enumerate(raw_files):
        if i == temp_ref:
            continue
        print(f"\r  预对齐: {i+1}/{n}", end="", flush=True)
        tgt_gray = read_raw_as_gray(f, half_size=half_size)
        dx, dy, _ = estimate_shift(temp_ref_gray, tgt_gray)
        if half_size:
            dx *= 2
            dy *= 2
        shifts[i] = (dx, dy)
        del tgt_gray

    print()

    # 找位移最接近中位数的帧
    shift_arr = np.array(shifts)
    median_shift = np.median(shift_arr, axis=0)

    # 计算每帧到中位数的距离
    dists = [np.linalg.norm(shift_arr[i] - median_shift) for i in range(n)]
    best_ref = int(np.argmin(dists))

    print(f"  最佳参考帧: {raw_files[best_ref].name} (位移 {shifts[best_ref][0]:.2f}, {shifts[best_ref][1]:.2f})")

    return best_ref


def align_all_frames(raw_files, ref_idx, half_size=True):
    """
    对齐所有帧到参考帧

    参数:
        raw_files: RAW 文件路径列表
        ref_idx: 参考帧索引
        half_size: 是否用半尺寸做对齐（快）

    返回:
        shifts: [(dx, dy), ...] 每帧位移
        sharp:  [score, ...] 每帧单帧锐度（LapVar2x，与 raw_files 同序，
                供选帧阶段的糊片门控使用；复用本函数已解码的灰度图，零额外成本）
    """
    n = len(raw_files)

    # 读参考帧灰度
    print(f"  参考帧: {raw_files[ref_idx].name}")
    ref_gray = read_raw_as_gray(raw_files[ref_idx], half_size=half_size)

    shifts = [(0.0, 0.0)] * n
    sharp = [0.0] * n
    sharp[ref_idx] = sharpness_score(ref_gray)

    for i, f in enumerate(raw_files):
        if i == ref_idx:
            continue

        tgt_gray = read_raw_as_gray(f, half_size=half_size)
        dx, dy, resp = estimate_shift(ref_gray, tgt_gray)

        # 如果半尺寸对齐，位移需要缩放
        if half_size:
            dx *= 2
            dy *= 2

        shifts[i] = (dx, dy)
        sharp[i] = sharpness_score(tgt_gray)

        dist = (dx**2 + dy**2)**0.5
        frac_x = abs(dx) % 1
        frac_y = abs(dy) % 1
        print(f"  [{i+1:2d}/{n}] {f.name:<25} dx={dx:+8.2f}  dy={dy:+8.2f}  dist={dist:7.2f}px  "
              f"亚像素=({frac_x:.3f},{frac_y:.3f})  响应={resp:.3f}")

        del tgt_gray

    print()
    return shifts, sharp
