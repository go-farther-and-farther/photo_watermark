#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手持多帧超分辨率合成工具

用法:
    python sr_process.py --input ./raw_burst/ --output result.tif
"""

import argparse
import gc
import time
from pathlib import Path

import numpy as np
import cv2
import tifffile
import rawpy

from align import align_all_frames, align_frame, find_best_ref_frame
from super_res import enhance_sharpness, denoise


def read_raw_full(raw_path):
    """读取完整 RGB（支持 RAW 和 JPG/PNG/TIFF）"""
    path = str(raw_path).lower()
    if path.endswith(('.jpg', '.jpeg', '.png', '.tif', '.tiff')):
        img = cv2.imread(str(raw_path), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise FileNotFoundError(f"无法读取: {raw_path}")
        if len(img.shape) == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
        else:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        if img.dtype == np.uint8:
            return img.astype(np.float32) / 255.0
        elif img.dtype == np.uint16:
            return img.astype(np.float32) / 65535.0
        else:
            return img.astype(np.float32)
    else:
        raw = rawpy.imread(str(raw_path))
        rgb = raw.postprocess(
            demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
            half_size=False,
            use_camera_wb=True,
            use_auto_wb=False,
            output_bps=16,
            output_color=rawpy.ColorSpace.sRGB,
            no_auto_bright=True,
            gamma=(1, 1),
        )
        return rgb.astype(np.float32) / 65535.0


def _parse_rational(s):
    """把 exifread 的值字符串解析为 (分子, 分母)，如 '1/250' 或 '0.005'"""
    s = str(s).strip()
    if '/' in s:
        a, b = s.split('/', 1)
        try:
            den = int(float(b))
            return (int(float(a)), den if den else 1)
        except ValueError:
            return None
    try:
        from fractions import Fraction
        fr = Fraction(s).limit_denominator(1000000)
        return (fr.numerator, fr.denominator)
    except (ValueError, ZeroDivisionError):
        return None


def build_tiff_exif_tags(exif_data):
    """
    生成 tifffile extratags（piexif 不支持 TIFF，只能走 extratags）

    说明：Make/Model/DateTime 是普通 TIFF tag；快门/ISO/镜头等在 Exif 子 IFD，
    tifffile 不支持直接写子 IFD，这些信息保留在 JPG 里；Software(305) 是
    tifffile 保留 tag，extratags 写不进去。
    """
    tags = []

    def add(code, value):
        if value:
            tags.append((code, 's', 0, str(value), False))

    add(271, exif_data.get("Image Make"))              # Make
    add(272, exif_data.get("Image Model"))             # Model
    add(306, exif_data.get("EXIF DateTimeOriginal"))   # DateTime
    return tags


def find_input_files(folder_path):
    """查找输入文件（RAW + JPG/PNG/TIFF）"""
    folder = Path(folder_path)
    files = []
    for ext in ('.arw', '.cr2', '.cr3', '.nef', '.orf', '.raf', '.dng', '.rw2',
                '.jpg', '.jpeg', '.png', '.tif', '.tiff'):
        files.extend(folder.glob(f"*{ext}"))
        files.extend(folder.glob(f"*{ext.upper()}"))
    files = sorted(set(files))
    if not files:
        raise FileNotFoundError(f"在 {folder_path} 中没有找到图片文件")
    return files


def select_best_frames(raw_files, shifts, ref_idx, max_frames=16, max_shift=50.0):
    """选择最佳帧"""
    n = len(raw_files)
    if n <= max_frames:
        return list(range(n)), raw_files, shifts, ref_idx

    # 过滤位移太大的帧
    candidates = []
    for i in range(n):
        dx, dy = shifts[i]
        dist = (dx**2 + dy**2)**0.5
        if dist <= max_shift or i == ref_idx:
            candidates.append(i)
        else:
            print(f"  过滤（位移{dist:.1f}px）: {raw_files[i].name}")

    if len(candidates) < 2:
        print(f"  警告: 过滤后只剩{len(candidates)}帧，放宽阈值")
        candidates = list(range(n))

    # 从候选中选亚像素分布最均匀的
    if len(candidates) <= max_frames:
        selected_indices = sorted(candidates)
    else:
        shift_arr = np.array(shifts)
        selected = [ref_idx]
        while len(selected) < max_frames:
            best_idx = -1
            best_min_dist = -1
            for i in candidates:
                if i in selected:
                    continue
                min_dist = min(
                    np.linalg.norm(shift_arr[i] - shift_arr[j])
                    for j in selected
                )
                if min_dist > best_min_dist:
                    best_min_dist = min_dist
                    best_idx = i
            if best_idx >= 0:
                selected.append(best_idx)
        selected_indices = sorted(selected)

    selected_files = [raw_files[i] for i in selected_indices]
    selected_shifts = [shifts[i] for i in selected_indices]
    new_ref_idx = selected_indices.index(ref_idx)

    dropped = [raw_files[i].name for i in range(n) if i not in selected_indices]
    if dropped:
        print(f"  跳过 {len(dropped)} 帧")

    return selected_indices, selected_files, selected_shifts, new_ref_idx


def _refine_shift(ref_gray, frame_gray, d, crop_ratio=0.5):
    """
    在全分辨率上精修帧间位移

    半尺寸相位相关的精度只有 ~0.2px，映射到全分辨率后误差放大一倍；
    用整数部分预平移取窗后再做一次全分辨率相位相关，把残差补回来。
    """
    h, w = ref_gray.shape
    ix, iy = int(round(d[0])), int(round(d[1]))
    ch, cw = int(h * crop_ratio), int(w * crop_ratio)
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    fy0 = int(np.clip(y0 + iy, 0, h - ch))
    fx0 = int(np.clip(x0 + ix, 0, w - cw))
    ref_c = ref_gray[y0:y0 + ch, x0:x0 + cw]
    frm_c = frame_gray[fy0:fy0 + ch, fx0:fx0 + cw]
    win = cv2.createHanningWindow((cw, ch), cv2.CV_32F)
    (rx, ry), _ = cv2.phaseCorrelate(ref_c * win, frm_c * win)
    return (ix + rx, iy + ry)


def _estimate_gain_bias(ref_gray, frame_gray, d):
    """
    估计帧间光度差异: frame ≈ g * ref + b

    连拍的曝光/增益漂移会让平均融合出现色鬼影，融合前先校正。
    取中间 96% 分位内的样本做最小二乘，抗运动区域干扰。
    """
    h, w = ref_gray.shape
    ch, cw = int(h * 0.5), int(w * 0.5)
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    ix, iy = int(round(d[0])), int(round(d[1]))
    fy0 = int(np.clip(y0 + iy, 0, h - ch))
    fx0 = int(np.clip(x0 + ix, 0, w - cw))
    a = ref_gray[y0:y0 + ch, x0:x0 + cw].ravel()
    b = frame_gray[fy0:fy0 + ch, fx0:fx0 + cw].ravel()
    # 先做大半径模糊: 残余错位(几像素)不会破坏低频光度关系
    a = cv2.GaussianBlur(a.reshape(ch, cw), (0, 0), 8).ravel()
    b = cv2.GaussianBlur(b.reshape(ch, cw), (0, 0), 8).ravel()
    step = max(1, a.size // 200000)
    a, b = a[::step], b[::step]
    lo, hi = np.percentile(a, [2, 98])
    m = (a > lo) & (a < hi)
    A = np.stack([a[m], np.ones(int(m.sum()))], axis=1)
    g, c = np.linalg.lstsq(A, b[m], rcond=None)[0]
    return float(np.clip(g, 0.9, 1.1)), float(np.clip(c, -0.05, 0.05))


def _refine_euclidean(ref_half, frm_half, dx_full, dy_full, crop_ratio=0.55):
    """
    半尺寸 ECC 欧氏精修（平移+旋转）

    手持连拍帧间除了平移还有小幅旋转：0.2° 在 8288px 宽度上就是边缘 ~15px
    的错位，纯平移模型必然出重影。dx_full/dy_full 为全分辨率平移初值。
    返回 (E 2x3: ref半尺寸crop -> frame半尺寸crop 的仿射, fx0, fy0, x0h, y0h)。
    """
    h, w = ref_half.shape
    dx, dy = dx_full / 2.0, dy_full / 2.0
    cc, cw = int(h * crop_ratio), int(w * crop_ratio)
    y0, x0 = (h - cc) // 2, (w - cw) // 2
    ix, iy = int(round(dx)), int(round(dy))
    fy0 = int(np.clip(y0 + iy, 0, h - cc))
    fx0 = int(np.clip(x0 + ix, 0, w - cw))
    ref_c = ref_half[y0:y0 + cc, x0:x0 + cw]
    frm_c = frm_half[fy0:fy0 + cc, fx0:fx0 + cw]
    warp = np.eye(2, 3, dtype=np.float32)
    _, warp = cv2.findTransformECC(ref_c, frm_c, warp, cv2.MOTION_EUCLIDEAN,
                                   (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-6),
                                   None, 3)
    return warp, fx0, fy0, x0, y0


def reconstruct(raw_files, shifts, ref_idx, scale, use_deghost, tile=1024):
    """
    超分辨率重建 v2：SR 网格直接融合

    旧版（reconstruct_legacy）先把每帧插值回参考帧网格再统一放大，
    亚像素相位信息在"对齐回参考帧"那一步就被抹掉，融合只剩降噪。

    这一版：
      1. 全分辨率精修每帧位移（补偿半尺寸对齐的残差）
      2. 估计并校正帧间光度增益/偏置
      3. 每帧按含小数部分的位移直接 remap 到超分网格——各帧采样
         落在超分网格的不同亚相位上，融合才可能真正增加细节
      4. 分块鲁棒融合：以逐像素中位数为锚压低离群帧（运动/鬼影），
         替代旧版的高斯掩码
    """
    n = len(raw_files)

    # 帧缓存：uint16（45MP 一帧 float32 要 540MB，16 帧装不下）
    print("  读入帧缓存 (uint16)...")
    frames = []
    for f in raw_files:
        img = read_raw_full(f)
        frames.append((np.clip(img, 0.0, 1.0) * 65535.0).astype(np.uint16))
        del img
        gc.collect()

    # 位移精修（平移+旋转）+ 光度校正参数
    ref_u16 = frames[ref_idx]
    h, w = ref_u16.shape[:2]
    ref_gray = np.power(np.clip(np.mean(ref_u16, axis=2, dtype=np.float32) / 65535.0, 0, 1), 1 / 2.2)
    ref_half = cv2.resize(ref_gray, (ref_gray.shape[1] // 2, ref_gray.shape[0] // 2))
    gains = np.ones(n, dtype=np.float32)
    biases = np.zeros(n, dtype=np.float32)
    affines = [np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32) for _ in range(n)]
    motion_frac = np.zeros(n, dtype=np.float32)
    max_rot = 0.0
    for i in range(n):
        if i == ref_idx:
            continue
        fg = np.power(np.clip(np.mean(frames[i], axis=2, dtype=np.float32) / 65535.0, 0, 1), 1 / 2.2)
        shifts[i] = _refine_shift(ref_gray, fg, shifts[i])
        gains[i], biases[i] = _estimate_gain_bias(ref_gray, fg, shifts[i])
        # ECC 欧氏精修（半尺寸）: 捕捉帧间旋转，纯平移模型下 0.2° = 边缘 ~15px 错位
        A = np.array([[1.0, 0.0, shifts[i][0]], [0.0, 1.0, shifts[i][1]]], dtype=np.float32)
        rot = 0.0
        # 半尺寸仿射（ref半尺寸坐标 -> frame半尺寸坐标），ECC 失败时退回纯平移
        Ph = np.array([[1.0, 0.0, shifts[i][0] / 2.0],
                       [0.0, 1.0, shifts[i][1] / 2.0],
                       [0.0, 0.0, 1.0]], dtype=np.float64)
        try:
            fg_half = cv2.resize(fg, (fg.shape[1] // 2, fg.shape[0] // 2))
            E, fx0, fy0, x0h, y0h = _refine_euclidean(ref_half, fg_half, *shifts[i])
            rot_raw = float(np.degrees(np.arctan2(E[1, 0], E[0, 0])))
            if abs(rot_raw) > 0.5 or abs(E[0, 2]) > 30 or abs(E[1, 2]) > 30:
                # 窗口已按整数平移预对齐，ECC 只应解出亚像素残差+微小旋转
                raise ValueError(f'ECC 解不合理(旋转 {rot_raw:+.2f}°, 平移 {E[0,2]:.1f},{E[1,2]:.1f})，退回纯平移')
            E3 = np.vstack([E, [0.0, 0.0, 1.0]])
            Th = np.array([[1, 0, fx0], [0, 1, fy0], [0, 0, 1]], dtype=np.float64)
            To = np.array([[1, 0, -x0h], [0, 1, -y0h], [0, 0, 1]], dtype=np.float64)
            Ph = Th @ E3 @ To                             # ref半尺寸坐标 -> frame半尺寸坐标
            S2 = np.diag([2.0, 2.0, 1.0])
            A3 = S2 @ Ph @ np.linalg.inv(S2)              # 半尺寸仿射 -> 全尺寸
            A = A3[:2].astype(np.float32)
            rot = float(np.degrees(np.arctan2(A[1, 0], A[0, 0])))
            max_rot = max(max_rot, abs(rot))
        except (cv2.error, ValueError):
            pass  # ECC 不收敛/解不合理时退回纯平移
        affines[i] = A
        # 内容运动检查: 对齐(平移+旋转)后仍大幅差异 = 人物/物体自身移动。
        # 无论 ECC 成败都必须执行（用最终仿射 Ph），否则坏帧会漏检。
        fg_half = cv2.resize(fg, (fg.shape[1] // 2, fg.shape[0] // 2))
        xs = np.arange(ref_half.shape[1], dtype=np.float32)
        ys = np.arange(ref_half.shape[0], dtype=np.float32)
        P32 = Ph.astype(np.float32)
        map_x = (P32[0, 0] * xs[None, :] + P32[0, 1] * ys[:, None] + P32[0, 2])
        map_y = (P32[1, 0] * xs[None, :] + P32[1, 1] * ys[:, None] + P32[1, 2])
        aligned = cv2.remap(fg_half, map_x, map_y, interpolation=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=-1.0)
        valid = aligned >= 0
        diff = np.abs(aligned - ref_half)
        motion_frac[i] = float(((diff > 0.06) & valid).sum() / max(valid.sum(), 1))
        print(f"  精修 {raw_files[i].name}: dx={A[0, 2]:+8.2f} dy={A[1, 2]:+8.2f} "
              f"旋转={rot:+7.3f}° 增益={gains[i]:.4f} 偏置={biases[i]:+.4f} "
              f"运动占比={motion_frac[i]:.1%}")
        del fg, fg_half
    del ref_gray, ref_half
    gc.collect()

    # 剔除运动帧: 对齐后差异区域超过 15% 的帧整帧不用（人物走动/动物等）。
    # 去鬼影依赖中位数锚定，至少需要 3 个一致帧，否则残余运动会以半强度鬼影出现。
    usable = [i for i in range(n) if motion_frac[i] <= 0.15]
    for i in range(n):
        if motion_frac[i] > 0.15:
            print(f"  [剔除] {raw_files[i].name}: 运动占比 {motion_frac[i]:.1%}，内容大幅移动")
    min_need = 3 if use_deghost else 2
    if len(usable) < min_need:
        raise ValueError(
            f'画面内容大幅移动（人物/动物等）：有效帧仅 {len(usable)} 张（需≥{min_need}），无法合成。\n'
            '建议: 该场景改用单张最佳帧，或提高快门速度/缩小画面占比重拍。')
    if len(usable) < n:
        gc.collect()
    gains_u = gains[usable]
    biases_u = biases[usable]

    # 重叠画布：场景点 s 需对每帧都满足 s + d ∈ [0, w)；旋转按内切矩形收缩画布
    min_dx, max_dx = min(s[0] for s in shifts), max(s[0] for s in shifts)
    min_dy, max_dy = min(s[1] for s in shifts), max(s[1] for s in shifts)
    rot_mx = int(np.ceil(np.tan(np.radians(max_rot)) * h / 2)) if max_rot > 0.05 else 0
    rot_my = int(np.ceil(np.tan(np.radians(max_rot)) * w / 2)) if max_rot > 0.05 else 0
    crop_x0 = max(0, int(np.ceil(-min_dx))) + rot_mx
    crop_x1 = min(w, int(np.floor(w - max_dx))) - rot_mx
    crop_y0 = max(0, int(np.ceil(-min_dy))) + rot_my
    crop_y1 = min(h, int(np.floor(h - max_dy))) - rot_my
    crop_w, crop_h = crop_x1 - crop_x0, crop_y1 - crop_y0
    if crop_w < 100 or crop_h < 100:
        print(f"  警告: 重叠区域太小 ({crop_w}x{crop_h})，使用全图")
        crop_x0 = crop_y0 = 0
        crop_w, crop_h = w, h

    sr_h, sr_w = crop_h * scale, crop_w * scale
    print(f"  原始: {w}x{h}")
    print(f"  重叠区域: ({crop_x0},{crop_y0}) {crop_w}x{crop_h}")
    print(f"  超分输出: {sr_w}x{sr_h}")

    # 分块鲁棒融合
    out = np.zeros((sr_h, sr_w, 3), dtype=np.uint16)
    sx_base = (np.arange(sr_w, dtype=np.float32) + 0.5) / scale - 0.5 + crop_x0
    sy_base = (np.arange(sr_h, dtype=np.float32) + 0.5) / scale - 0.5 + crop_y0
    robust = use_deghost and n >= 3
    for ty in range(0, sr_h, tile):
        th = min(tile, sr_h - ty)
        for tx in range(0, sr_w, tile):
            tw = min(tile, sr_w - tx)
            SX, SY = np.meshgrid(sx_base[tx:tx + tw], sy_base[ty:ty + th])
            S = np.empty((len(usable), th, tw, 3), dtype=np.float32)
            for j, i in enumerate(usable):
                A = affines[i]
                mx = (A[0, 0] * SX + A[0, 1] * SY + A[0, 2]).astype(np.float32)
                my = (A[1, 0] * SX + A[1, 1] * SY + A[1, 2]).astype(np.float32)
                S[j] = cv2.remap(frames[i], mx, my, interpolation=cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_REPLICATE)
            S -= biases_u[:, None, None, None]
            S *= gains_u[:, None, None, None]
            if robust:
                med = np.median(S, axis=0)
                rel = np.abs(S - med[None]).mean(axis=3) / (1310.0 + med.mean(axis=2))
                wmat = 1.0 / (1.0 + (rel / 0.25) ** 2)
                val = (S * wmat[..., None]).sum(axis=0) / np.maximum(wmat.sum(axis=0), 1e-6)[..., None]
            else:
                val = S.mean(axis=0)
            out[ty:ty + th, tx:tx + tw] = np.clip(val + 0.5, 0, 65535).astype(np.uint16)
        print(f"\r  融合: {min(ty + tile, sr_h)}/{sr_h} 行", end="", flush=True)
    print()
    del frames
    gc.collect()

    return out.astype(np.float32) / 65535.0, (w, h)


def reconstruct_legacy(raw_files, shifts, ref_idx, scale, use_deghost):
    """
    旧版重建：先对齐回参考帧网格再统一放大（保留用于 A/B 对比）

    已知缺陷：对齐插值抹掉亚像素相位信息，融合只降噪不加细节。
    """
    n = len(raw_files)

    # 读参考帧获取尺寸
    ref_img = read_raw_full(raw_files[ref_idx])
    h, w = ref_img.shape[:2]

    # 计算重叠区域（位移的交集）
    min_dx = min(s[0] for s in shifts)
    max_dx = max(s[0] for s in shifts)
    min_dy = min(s[1] for s in shifts)
    max_dy = max(s[1] for s in shifts)

    crop_x0 = max(0, int(np.ceil(max_dx)))
    crop_y0 = max(0, int(np.ceil(max_dy)))
    crop_x1 = min(w, w + int(np.floor(min_dx)))
    crop_y1 = min(h, h + int(np.floor(min_dy)))

    crop_w = crop_x1 - crop_x0
    crop_h = crop_y1 - crop_y0

    if crop_w < 100 or crop_h < 100:
        print(f"  警告: 重叠区域太小 ({crop_w}x{crop_h})，使用全图")
        crop_x0, crop_y0 = 0, 0
        crop_w, crop_h = w, h

    print(f"  原始: {w}x{h}")
    print(f"  重叠区域: ({crop_x0},{crop_y0}) {crop_w}x{crop_h}")

    sr_h, sr_w = crop_h * scale, crop_w * scale
    print(f"  超分输出: {sr_w}x{sr_h}")

    sr_sum = np.zeros((sr_h, sr_w, 3), dtype=np.float64)
    sr_weight = np.zeros((sr_h, sr_w), dtype=np.float64)

    # 参考帧在超分网格上的投影（去鬼影比对基准），循环外只算一次
    ref_sr = None
    if use_deghost:
        ref_cropped = ref_img[crop_y0:crop_y0+crop_h, crop_x0:crop_x0+crop_w]
        ref_sr = cv2.resize(ref_cropped, (sr_w, sr_h), interpolation=cv2.INTER_CUBIC)
        del ref_cropped

    for i, f in enumerate(raw_files):
        print(f"\r  融合: {i+1}/{n} {f.name}", end="", flush=True)

        dx, dy = shifts[i]

        if i == ref_idx:
            frame = ref_img
        else:
            frame = read_raw_full(f)

        # 对齐到参考帧
        aligned = align_frame(ref_img, frame, dx, dy)

        if i != ref_idx:
            del frame

        # 裁切到重叠区域
        cropped = aligned[crop_y0:crop_y0+crop_h, crop_x0:crop_x0+crop_w]
        del aligned

        # 上采样到超分网格
        sr_frame = cv2.resize(cropped, (sr_w, sr_h), interpolation=cv2.INTER_CUBIC)
        del cropped

        # 去鬼影
        if use_deghost and i != ref_idx:
            diff = np.mean(np.abs(sr_frame - ref_sr), axis=2)
            motion = (diff > 30 / 255.0).astype(np.float32)
            motion = cv2.GaussianBlur(motion, (11, 11), 3)
            weight = 1.0 - motion * 0.8
        else:
            weight = np.ones((sr_h, sr_w), dtype=np.float32)

        for c in range(3):
            sr_sum[:, :, c] += sr_frame[:, :, c] * weight
        sr_weight += weight

        del sr_frame, weight
        gc.collect()

    print()

    del ref_img
    if ref_sr is not None:
        del ref_sr
    gc.collect()

    sr_weight = np.maximum(sr_weight, 1e-8)
    sr_result = (sr_sum / sr_weight[:, :, np.newaxis]).astype(np.float32)

    del sr_sum, sr_weight
    gc.collect()

    return sr_result, (w, h)


def process(input_folder, output_path, scale=2, max_frames=16, max_shift=50.0,
            use_deghost=True, sharpen=0.3, use_denoise=False, denoise_strength=5,
            use_legacy=False, tile=1024, threads=0, master_scale=None):
    if threads and threads > 0:
        # 限制计算线程数（如只用 P 核 8 线程，控制功耗温度；0 = 全部核心）
        cv2.setNumThreads(int(threads))
    print("=" * 60)
    print("手持多帧超分辨率合成")
    print("=" * 60)

    t0 = time.time()

    raw_files = find_input_files(input_folder)
    n = len(raw_files)
    print(f"  文件数: {n}")

    # 找最佳参考帧 + 对齐
    print("\n[1/4] 找最佳参考帧并对齐...")
    ref_idx = find_best_ref_frame(raw_files, half_size=True)
    shifts = align_all_frames(raw_files, ref_idx, half_size=True)

    # 打印位移统计
    dxs = [s[0] for s in shifts]
    dys = [s[1] for s in shifts]
    dists = [(s[0]**2+s[1]**2)**0.5 for s in shifts]
    print(f"  位移范围: dx [{min(dxs):.2f}, {max(dxs):.2f}], dy [{min(dys):.2f}, {max(dys):.2f}]")
    print(f"  距离范围: {min(dists):.2f} ~ {max(dists):.2f} px")
    print(f"  亚像素分布:")
    for i, (f, (dx, dy)) in enumerate(zip(raw_files, shifts)):
        frac_x = abs(dx) % 1
        frac_y = abs(dy) % 1
        dist = (dx**2+dy**2)**0.5
        print(f"    {f.name}: dx={dx:+.2f} dy={dy:+.2f} dist={dist:.2f}px  "
              f"亚像素=({frac_x:.3f},{frac_y:.3f})")

    # 选择最佳帧
    print(f"\n[2/4] 选择最佳 {max_frames} 帧...")
    selected_indices, raw_files, shifts, ref_idx = select_best_frames(
        raw_files, shifts, ref_idx, max_frames, max_shift
    )
    print(f"  使用 {len(raw_files)} 帧, 参考帧: {ref_idx}")

    # 超分重建
    print(f"\n[3/4] 超分辨率重建 (x{scale}, {'旧版对齐融合' if use_legacy else 'SR网格鲁棒融合'})...")
    if use_legacy:
        sr_result, (orig_w, orig_h) = reconstruct_legacy(raw_files, shifts, ref_idx, scale, use_deghost)
    else:
        sr_result, (orig_w, orig_h) = reconstruct(raw_files, shifts, ref_idx, scale, use_deghost, tile)

    # 后处理
    print("\n[4/4] 后处理...")
    if sharpen > 0:
        sr_result = enhance_sharpness(sr_result, strength=sharpen)
    if use_denoise:
        sr_result = denoise(sr_result, strength=denoise_strength)

    # sRGB gamma + 自动亮度
    sr_result = np.clip(sr_result, 0, 1)
    sr_result = np.power(sr_result, 1.0 / 2.2)

    # 自动亮度：拉伸到 0-1 范围（去掉最暗和最亮的 0.5%）
    for c in range(3):
        ch = sr_result[:, :, c]
        lo = np.percentile(ch, 0.5)
        hi = np.percentile(ch, 99.5)
        sr_result[:, :, c] = np.clip((ch - lo) / (hi - lo + 1e-8), 0, 1)

    # 从参考帧读取 EXIF
    exif_data = {}
    try:
        import exifread
        ref_path = str(raw_files[ref_idx])
        with open(ref_path, 'rb') as f:
            tags = exifread.process_file(f, details=False)
            for key, val in tags.items():
                exif_data[key] = str(val)
    except Exception:
        pass

    # 构造 JPG 的 EXIF（piexif 只支持 JPEG；快门/ISO/镜头等完整信息保留在 JPG）
    exif_bytes = None
    try:
        import piexif

        def to_int(s):
            try:
                return int(float(str(s).split(',')[0].split('/')[0]))
            except (ValueError, TypeError):
                return None

        zeroth = {
            piexif.ImageIFD.Make: exif_data.get("Image Make", "").encode(),
            piexif.ImageIFD.Model: exif_data.get("Image Model", "").encode(),
            piexif.ImageIFD.Software: b"SuperRes Tool",
        }
        exif = {}
        if "EXIF DateTimeOriginal" in exif_data:
            exif[piexif.ExifIFD.DateTimeOriginal] = exif_data["EXIF DateTimeOriginal"].encode()
        if "EXIF ExposureTime" in exif_data:
            r = _parse_rational(exif_data["EXIF ExposureTime"])
            if r:
                exif[piexif.ExifIFD.ExposureTime] = r
        iso = to_int(exif_data.get("EXIF ISOSpeedRatings"))
        if iso is not None:
            exif[piexif.ExifIFD.ISOSpeedRatings] = iso
        if "EXIF FocalLength" in exif_data:
            r = _parse_rational(exif_data["EXIF FocalLength"])
            if r:
                exif[piexif.ExifIFD.FocalLength] = r
        f35 = to_int(exif_data.get("EXIF FocalLengthIn35mmFilm"))
        if f35 is not None:
            exif[piexif.ExifIFD.FocalLengthIn35mmFilm] = f35
        if "EXIF LensModel" in exif_data:
            exif[piexif.ExifIFD.LensModel] = exif_data["EXIF LensModel"].encode()
        if "EXIF LensMake" in exif_data:
            exif[piexif.ExifIFD.LensMake] = exif_data["EXIF LensMake"].encode()

        exif_bytes = piexif.dump({"0th": zeroth, "Exif": exif})
    except Exception as e:
        print(f"  JPG EXIF 构造失败: {e}")

    # 保存调色母版与 JPG（母版默认 1.5x 压缩 TIF，LR/PS 可完整调色；JPG 全尺寸分享用）
    base = str(output_path).rsplit('.', 1)[0]
    exif_tags = build_tiff_exif_tags(exif_data)
    if master_scale and float(master_scale) < float(scale):
        k = float(master_scale) / float(scale)
        mh, mw = round(sr_result.shape[0] * k), round(sr_result.shape[1] * k)
        master = cv2.resize(sr_result, (mw, mh), interpolation=cv2.INTER_AREA)
        master = (np.clip(master, 0, 1) * 65535).astype(np.uint16)
        master_path = f'{base}_{master_scale}x.tif'
        # zlib 无损压缩: 1.5x 母版约 250-350MB（全尺寸 2x 无压缩要 1GB）
        tifffile.imwrite(master_path, master, extratags=exif_tags, compression='zlib')
        print(f"  调色母版 TIF: {master_path} ({mw}x{mh}, "
              f"{Path(master_path).stat().st_size/(1024*1024):.0f}MB)")
    else:
        # tifffile 按数组通道顺序原样写入，必须保持 RGB（不能转 BGR，否则红蓝互换）
        tiff_path = base + '.tif'
        img_tiff = (np.clip(sr_result, 0, 1) * 65535).astype(np.uint16)
        tifffile.imwrite(tiff_path, img_tiff, extratags=exif_tags)
        tiff_size = Path(tiff_path).stat().st_size / (1024*1024)
        print(f"  TIFF: {tiff_path} ({tiff_size:.0f}MB)")

    # 保存 JPG（预览用，复用 TIFF 的 EXIF）
    jpg_path = base + f'_{scale}x.jpg'
    img_jpg = (np.clip(sr_result, 0, 1) * 255).astype(np.uint8)
    img_jpg = cv2.cvtColor(img_jpg, cv2.COLOR_RGB2BGR)
    # cv2.imwrite 对非 ASCII 路径会静默失败，用 imencode + 直接写文件
    ok, jpg_buf = cv2.imencode('.jpg', img_jpg, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if ok:
        Path(jpg_path).write_bytes(jpg_buf.tobytes())
    else:
        print(f"  警告: JPG 编码失败")

    # 复制 EXIF 到 JPG
    if exif_bytes:
        try:
            piexif.insert(exif_bytes, jpg_path)
        except Exception as e:
            print(f"  JPG EXIF 写入失败: {e}")

    jpg_size = Path(jpg_path).stat().st_size / (1024*1024)
    print(f"  JPG:  {jpg_path} ({jpg_size:.0f}MB)")

    elapsed = time.time() - t0
    sr_h, sr_w = sr_result.shape[:2]
    print(f"\n{'=' * 60}")
    print(f"完成！ {orig_w}x{orig_h} → {sr_w}x{sr_h}")
    print(f"耗时: {elapsed:.1f} 秒")
    print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(description="手持多帧超分辨率合成")
    parser.add_argument("--input", "-i", required=True, help="RAW 文件夹")
    parser.add_argument("--output", "-o", default="result.tif", help="输出路径")
    parser.add_argument("--scale", "-s", type=int, default=2, help="倍率")
    parser.add_argument("--max-frames", "-n", type=int, default=16, help="最大使用帧数")
    parser.add_argument("--max-shift", type=float, default=50.0, help="最大允许位移(像素)")
    parser.add_argument("--no-deghost", action="store_true", help="禁用去鬼影")
    parser.add_argument("--sharpen", type=float, default=0.3, help="锐化强度")
    parser.add_argument("--denoise", action="store_true", help="启用降噪")
    parser.add_argument("--legacy", action="store_true", help="使用旧版算法（对齐回参考帧再放大）")
    parser.add_argument("--tile", type=int, default=1024, help="融合分块大小（像素）")

    args = parser.parse_args()
    process(
        args.input, args.output, args.scale, args.max_frames, args.max_shift,
        not args.no_deghost, args.sharpen, args.denoise, 5, args.legacy, args.tile
    )


if __name__ == "__main__":
    main()
