#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手持多帧超分辨率合成工具

用法:
    python sr_process.py --input ./raw_burst/ --output result.tif
"""

import argparse
import gc
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import cv2
import tifffile
import rawpy

from align import align_all_frames, align_frame, find_best_ref_frame, read_raw_as_gray
from super_res import enhance_sharpness, denoise


def read_raw_full(raw_path, camera_space=False):
    """读取完整 RGB（支持 RAW 和 JPG/PNG/TIFF）

    camera_space=True 时 RAW 走线性相机原生色彩空间（Linear DNG 融合用），
    JPG/PNG/TIFF 无相机空间，忽略该参数。
    """
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
        if camera_space:
            return read_raw_camera_linear(raw_path)
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


def copy_exif_from_frame(exiftool, ref_path, target_path, width, height):
    """把参考帧完整 EXIF（含镜头型号、快门、ISO 等）用 ExifTool 拷到输出文件

    JPG/TIFF/DNG 均可（ExifTool 能写 TIFF 的 Exif 子 IFD，补上 tifffile 的短板）；
    Lightroom 依据 LensModel/LensMake 自动挂镜头矫正配置。
    拷贝时排除并改写尺寸标签（输出分辨率与源帧不同）；**排除 MakerNotes 整块移植**
    ——NEF 的 makernote 内含预览指针，跨文件移植后指向零长度数据
    （PreviewImageLength=0），Lightroom 的严格解析器会因此拒收整个 DNG；
    同时排除 CFAPattern（Bayer 排布对 LinearRaw 无意义，纯残留）。
    失败返回 False（调用方回退基础 EXIF）。
    """
    import subprocess
    cmd = [exiftool, '-tagsfromfile', str(ref_path),
           '-EXIF:all', '--MakerNotes', '--PreviewImage', '--CFAPattern',
           '--EXIF:ExifImageWidth', '--EXIF:ExifImageHeight',
           f'-ExifImageWidth={width}', f'-ExifImageHeight={height}',
           '-overwrite_original', '-q', str(target_path)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# ===== Linear DNG 母版输出（LR 可调 Kelvin 色温）=====
# 设计见 docs/superpowers/specs/2026-10-16-linear-dng-design.md

RAW_EXTS = ('.arw', '.cr2', '.cr3', '.nef', '.orf', '.raf', '.dng', '.rw2')


def neutral_from_cam_mul(cam_mul):
    """rawpy camera_whitebalance(R,G,B,G2) -> AsShotNeutral（取倒数）

    仅作末级兜底：假设数据未烙任何 WB（纯相机原生域）才成立；本管线数据烙有
    daylight 乘数，精确值应走 as_shot_neutral_from_metadata / 实测比值。
    """
    m = [float(v) for v in list(cam_mul)[:3]]
    if len(m) < 3 or min(m) <= 0:
        return None
    return [1.0 / v for v in m]


def read_raw_camera_linear(raw_path, half_size=False, use_camera_wb=False):
    """线性相机原生 RGB 解码（ColorSpace.raw、gamma=1，供 Linear DNG 融合）

    use_camera_wb=False（融合数据域）时 libraw 会把 daylight 乘数烙进数据——
    除回去得到相机原生域（undo = max(day)/day，仅 nocam 分支）：DNG 母版存
    原生域，AsShotNeutral=1/cam_mul 才落在 LR 色温滑块中段（烙 daylight 时
    标签被顶到 2250K、贴近滑块下限）。undo 与 libraw 实际烙入量在 B 通道有
    ~4% 级偏差，由 AsShotNeutral 的实测链路（measure_as_shot_neutral）吸收。
    """
    import rawpy
    raw = rawpy.imread(str(raw_path))
    try:
        rgb = raw.postprocess(
            demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
            half_size=half_size,
            use_camera_wb=use_camera_wb,
            use_auto_wb=False,
            output_bps=16,
            output_color=rawpy.ColorSpace.raw,
            no_auto_bright=True,
            gamma=(1, 1),
        )
        rgb = rgb.astype(np.float32) / 65535.0
        if not use_camera_wb:
            day = np.asarray(raw.daylight_whitebalance[:3], np.float32)
            if day.shape == (3,) and np.isfinite(day).all() and day.min() > 0:
                rgb = rgb * (day.max() / day)
            rgb = np.clip(rgb, 0.0, 1.0)
        return rgb
    finally:
        raw.close()


def measure_as_shot_neutral(ref_path):
    """实测 AsShotNeutral：同帧两种 WB 解码的通道中位数比值（nocam/cam）

    nocam 分支已除回 daylight 烙印（原生域），其灰色 ∝ 1/cam_mul，
    故 AsShotNeutral = median(nocam)/median(cam) ≈ 1/cam_mul（Kelvin 落中段）。
    **注意是 nocam 在分子**：此前写成 cam/nocam 导致标签整组取倒，LR 渲染
    强偏黄、色温滑块被顶到极端（2026-10-16 实测）。
    连 libraw 内部归一化细节一起消掉；异常返回 None（回退 1/cam_mul 换算）。
    """
    try:
        nocam = read_raw_camera_linear(ref_path, half_size=True, use_camera_wb=False)
        cam = read_raw_camera_linear(ref_path, half_size=True, use_camera_wb=True)
        mn = np.median(nocam.reshape(-1, 3), axis=0)
        mc = np.median(cam.reshape(-1, 3), axis=0)
        ratio = mn / np.maximum(mc, 1e-6)
        if not np.isfinite(ratio).all() or ratio[1] <= 0:
            return None
        ratio = ratio / ratio[1]   # Adobe 惯例 G=1（量纲只取比值，LR 内部亦归一）
        return [float(np.clip(v, 0.25, 4.0)) for v in ratio]
    except Exception:
        return None


def extract_dng_metadata(raw_path):
    """从 RAW 提取 DNG 色彩元数据（XYZ→相机矩阵 + 机型）；非 RAW 或异常返回 None"""
    try:
        import rawpy
        with rawpy.imread(str(raw_path)) as raw:
            m = np.array(raw.rgb_xyz_matrix, dtype=np.float64)[:3, :3]
            if m.shape != (3, 3) or not np.isfinite(m).all() or abs(np.linalg.det(m)) < 1e-6:
                return None
        # 机型从 EXIF 读（rawpy 0.27 的 other 结构不含 make/model）
        import exifread
        with open(str(raw_path), 'rb') as f:
            tags = exifread.process_file(f, details=False, stop_tag='Image Model')
        model = str(tags.get('Image Model', '')).strip()
        if not model:
            return None
        return {'matrix': m.tolist(), 'model': model}
    except Exception:
        return None


def derive_camera_to_srgb(ref_path):
    """实测「相机原生RGB(nocam域) -> 线性sRGB(含as-shot WB)」的仿射变换 (A, b)

    同一帧解码两次（ColorSpace.raw 不带 WB / sRGB 带 WB，同 gamma=1），对中亮度
    非饱和像素做带截距的最小二乘回归。libraw 的真实变换与理论公式
    （inv(cam_xyz)@标准sRGB矩阵）不一致（理论路线强洋红偏色），且两种解码
    的黑电平处理不同导致存在截距——仿射回归把矩阵约定与偏移全部吸收。
    返回 (A, b)：linear_srgb ≈ nocam @ A.T + b。异常返回 None。
    """
    try:
        import rawpy
        with rawpy.imread(str(ref_path)) as raw:
            kw = dict(use_auto_wb=False, half_size=True,
                      output_bps=16, no_auto_bright=True, gamma=(1, 1))
            N = raw.postprocess(use_camera_wb=False, output_color=rawpy.ColorSpace.raw,
                                **kw).astype(np.float64) / 65535.0
            # nocam 分支与 read_raw_camera_linear 一致：除回 daylight 烙印（原生域），
            # 否则回归出的仿射与融合数据（已 undo）不匹配
            day = np.asarray(raw.daylight_whitebalance[:3], np.float64)
            if day.shape == (3,) and np.isfinite(day).all() and day.min() > 0:
                N = np.clip(N * (day.max() / day), 0.0, 1.0)
            L = raw.postprocess(use_camera_wb=True, output_color=rawpy.ColorSpace.sRGB,
                                **kw).astype(np.float64) / 65535.0
        n, l = N.reshape(-1, 3), L.reshape(-1, 3)
        lum = l.mean(axis=1)
        mask = (lum > np.percentile(lum, 25)) & (lum < 0.98) & (n.max(axis=1) < 0.98)
        X = np.hstack([n[mask], np.ones((int(mask.sum()), 1))])
        coef, *_ = np.linalg.lstsq(X, l[mask], rcond=None)
        A, b = coef[:3].T, coef[3]
        if not (np.isfinite(A).all() and np.isfinite(b).all()):
            return None
        return A, b
    except Exception:
        return None


def _worker_count(threads):
    """并行块数：threads>0 用之，0=全部核心；上限 8（每 worker 峰值内存数百 MB）"""
    n = threads if threads and threads > 0 else (os.cpu_count() or 4)
    return max(1, min(int(n), 8))


def rl_deconvolve_u16(arr_u16, iterations=12, sigma=1.0, tile=1024, halo=48, workers=0):
    """Richardson-Lucy 反卷积（高斯 PSF），分块流式，uint16 进出

    JPG/预览的可选锐化：反卷积恢复单帧共有的光学+手持模糊（对齐/选帧都无法
    修复的分量），锐度可到 SRHQ v2.5 水平。母版 DNG 保持线性原样——锐化是
    渲染观感，不进 raw 数据。分块+halo：97MP 全幅三次浮点副本会吃 4GB+，
    流式后峰值 <300MB；halo 远大于 3σ，核心区无边界伪迹。
    迭代间串行（12 轮依赖上一轮），迭代内的块互相独立——多线程并行。
    """
    obs = arr_u16.astype(np.float32) / 65535.0
    est = obs.copy()
    h, w = est.shape[:2]
    eps = 1e-4
    blocks = [(y0, x0) for y0 in range(0, h, tile) for x0 in range(0, w, tile)]
    nw = _worker_count(workers)
    cv_prev = cv2.getNumThreads()
    if nw > 1:
        cv2.setNumThreads(1)   # 块间并行，块内 cv2 不再叠线程（避免超订）
    try:
        for _ in range(iterations):
            prev = est
            est = np.empty_like(est)

            def rl_block(y0, x0):
                y1, x1 = min(y0 + tile, h), min(x0 + tile, w)
                ya, yb = max(0, y0 - halo), min(h, y1 + halo)
                xa, xb = max(0, x0 - halo), min(w, x1 + halo)
                e = prev[ya:yb, xa:xb]
                o = obs[ya:yb, xa:xb]
                dist = cv2.GaussianBlur(e, (0, 0), sigma)
                ratio = np.minimum(o / np.maximum(dist, eps), 4.0)   # 上限防暗部噪声被逐轮放大
                corr = cv2.GaussianBlur(ratio, (0, 0), sigma)
                return y0, x0, np.clip(
                    e[y0-ya:y1-ya, x0-xa:x1-xa] * corr[y0-ya:y1-ya, x0-xa:x1-xa],
                    0.0, 1.0)

            if nw > 1:
                with ThreadPoolExecutor(max_workers=nw) as ex:
                    for y0, x0, blk in ex.map(lambda b: rl_block(b[0], b[1]), blocks):
                        est[y0:y0 + min(tile, h - y0), x0:x0 + min(tile, w - x0)] = blk
            else:
                for y0, x0 in blocks:
                    _, _, blk = rl_block(y0, x0)
                    est[y0:y0 + min(tile, h - y0), x0:x0 + min(tile, w - x0)] = blk
            del prev
            gc.collect()
    finally:
        cv2.setNumThreads(cv_prev)
    return (est * 65535.0 + 0.5).astype(np.uint16)


def _chroma_denoise(rgb, shift=1):
    """色度降噪：色差通道降采样+轻微模糊后还原。

    多帧平均后残余噪点以色彩噪斑为主（亮度颗粒观感可接受）——色度带宽本来就低，
    降采样处理几乎无成本、不伤亮度结构。
    """
    rgb = rgb.astype(np.float32)
    y = rgb.mean(axis=2)
    cb = rgb[:, :, 2] - y
    cr = rgb[:, :, 0] - y
    f = 2 ** shift
    h, w = y.shape
    hs, ws = max(h // f, 1), max(w // f, 1)
    cb_s = cv2.GaussianBlur(cv2.resize(cb, (ws, hs), interpolation=cv2.INTER_AREA), (0, 0), 1.0)
    cr_s = cv2.GaussianBlur(cv2.resize(cr, (ws, hs), interpolation=cv2.INTER_AREA), (0, 0), 1.0)
    cb = cv2.resize(cb_s, (w, h), interpolation=cv2.INTER_LINEAR)
    cr = cv2.resize(cr_s, (w, h), interpolation=cv2.INTER_LINEAR)
    out = np.empty_like(rgb)
    out[:, :, 0] = y + cr
    out[:, :, 1] = y
    out[:, :, 2] = y + cb
    return np.clip(out, 0, 1)


def render_srgb_preview(linear_nocam, cam_to_srgb, stretch=True):
    """融合后的 nocam 域线性数据 -> 线性sRGB（实测仿射）-> 2.2 幂 ->（可选）逐通道拉伸

    stretch=True（默认）：旧 sRGB TIF 管线观感口径（gamma 2.2 + 0.5-99.5 拉伸），
    独立 JPG 预览用，暗场提亮。
    stretch=False：LR 默认渲染口径（只 gamma 不拉伸）——DNG 内嵌预览用。
    Windows 图片看的是内嵌预览，拉伸版会和 LR 的 raw 渲染明显不一致（实测）。
    cam_to_srgb 为 derive_camera_to_srgb 返回的 (A, b)。

    末尾两步修复（均为预览观感，不影响 DNG 数据）：
    - 高光去色：通道裁剪差异会让过曝区偏紫/偏粉（LR 有高光修复所以正常），
      按最大通道向中性白收敛，亮度保留、色度归零
    - 色度降噪：压掉色彩噪斑
    """
    A, b = cam_to_srgb
    srgb = np.clip(linear_nocam.astype(np.float64) @ np.asarray(A, np.float64).T
                   + np.asarray(b, np.float64), 0, 1)
    out = np.power(srgb, 1.0 / 2.2)
    if stretch:
        # 亮度单增益拉伸：从亮度百分位算统一 offset+gain，三通道共用。
        # 不能逐通道拉伸——各通道直方图不同，会掰歪色度（实测近距组白墙发绿）
        luma = out[:, :, 0] * 0.2126 + out[:, :, 1] * 0.7152 + out[:, :, 2] * 0.0722
        lo, hi = np.percentile(luma, 0.5), np.percentile(luma, 99.5)
        out = np.clip((out - lo) / (hi - lo + 1e-8), 0, 1)
    out = _chroma_denoise(out.astype(np.float32))
    # 高光去色：0.88（伽马后）起向中性白平滑收敛
    mx = out.max(axis=2)
    k = np.clip((mx - 0.88) / (1.0 - 0.88), 0.0, 1.0)
    k = k * k * (3.0 - 2.0 * k)
    if k.any():
        out = out * (1.0 - k[..., None]) + mx[..., None] * k[..., None]
    return (out * 255 + 0.5).astype(np.uint8)


def write_linear_dng(path, arr_u16, color_matrix, as_shot_neutral, camera_model,
                     exiftool, preview_rgb=None, orig_raw_name=None):
    """写 Linear DNG：Adobe 结构（IFD0 预览 + SubIFD LinearRaw 512 瓦片），ExifTool 补标签

    LR/ACR 打开后即为 raw：Kelvin 色温、相机配置文件、镜头矫正全部可用。
    结构必须遵 Adobe 惯例：raw 在 SubIFD（NewSubfileType=0）、IFD0 放 RGB 预览
    （NewSubfileType=1）、raw 数据 512 瓦片分块——单 IFD 裸结构（raw 直接在 IFD0、
    单条巨 strip）Lightroom 一律拒收（实测同标签集下 Adobe 结构可导入、单 IFD 被拒）。
    压缩必须无压缩（~600MB@24MP×2）——Lightroom 的 DNG 读取器对 LinearRaw 只
    稳定支持 JPEG-LL/无压缩，Adobe Deflate 会被拒收（实测打不开）。

    exiftool 写 DNG 标签的坑（exiftool 13.59 实测）：
    - BlackLevel/WhiteLevel/DefaultCrop*/ActiveArea 等 raw 几何标签被路由到 SubIFD
      组且要求特定值数量（BlackLevel 4 值、WhiteLevel 按通道数），不符即静默丢弃；
      其缺省值恰为本管线数据（黑 0/白满/无裁剪），故干脆不写。
    - 数值枚举必须用 `TAG#=` 语法：`-Orientation=1` 经 print 转换会写成 3；
      全局 `-n` 则会连累 `-CalibrationIlluminant1=D65`（解析成 0xD65=3429）和
      `-DNGVersion=1.4.0.0`（解析失败整条丢弃）。
    preview_rgb 为 sRGB u8（RGB 序），超宽自动缩到 1600px；None 时退化为对 raw
    直接 gamma（仅供无预览管线的兜底）。orig_raw_name 写入 OriginalRawFileName。
    """
    import subprocess
    import uuid
    path = Path(path)
    h, w = arr_u16.shape[:2]
    if preview_rgb is None:
        prev = (np.clip(arr_u16.astype(np.float32) / 65535.0, 0, 1) ** (1 / 2.2)
                * 255 + 0.5).astype(np.uint8)
    else:
        prev = np.ascontiguousarray(preview_rgb)
        if prev.shape[1] > 1600:
            ph = max(1, round(prev.shape[0] * 1600 / prev.shape[1]))
            prev = cv2.resize(prev, (1600, ph), interpolation=cv2.INTER_AREA)
    tile = min(512, -(-max(w, h) // 16) * 16)  # 瓦片须为 16 的倍数且小于图时取整图
    with tifffile.TiffWriter(str(path)) as tw:
        tw.write(prev, photometric=2, compression=None, planarconfig='CONTIG',
                 subfiletype=1, subifds=1, resolution=(72, 72))
        tw.write(arr_u16, photometric=34892, compression=None, planarconfig='CONTIG',
                 subfiletype=0, tile=(tile, tile))
    m = ' '.join(f'{float(v):.6g}' for v in np.asarray(color_matrix).ravel())
    n = ' '.join(f'{float(v):.6g}' for v in as_shot_neutral)
    cmd = [exiftool,
           '-DNGVersion=1.4.0.0',
           '-DNGBackwardVersion=1.4.0.0',
           f'-UniqueCameraModel={camera_model}',
           f'-ColorMatrix1={m}',
           '-CalibrationIlluminant1=D65',
           f'-AsShotNeutral={n}',
           '-BaselineExposure=0', '-BaselineNoise=0', '-BaselineSharpness=0',
           f'-RawDataUniqueID={uuid.uuid4().hex}',
           '-IFD0:Orientation#=1',
           '-overwrite_original', '-q', str(path)]
    if orig_raw_name:
        cmd.insert(len(cmd) - 2, f'-OriginalRawFileName={orig_raw_name}')
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


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


def select_best_frames(raw_files, shifts, ref_idx, max_frames=16, max_shift=150.0):
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


def _affine_coords(A, SX, SY):
    """仿射 warp 的目标坐标（cv2.remap 口径：输出坐标 -> 输入坐标）"""
    return (A[0, 0] * SX + A[0, 1] * SY + A[0, 2],
            A[1, 0] * SX + A[1, 1] * SY + A[1, 2])


def _proj_coords(H, SX, SY):
    """射影 warp 的目标坐标：坐标除以齐次项（设计上与 _affine_coords 同方向）"""
    d = H[2, 0] * SX + H[2, 1] * SY + H[2, 2]
    return ((H[0, 0] * SX + H[0, 1] * SY + H[0, 2]) / d,
            (H[1, 0] * SX + H[1, 1] * SY + H[1, 2]) / d)


def _persp_disp(H, hw):
    """H 与去透视仿射对四角投影的最大位移（px，H 归一化 h22=1 后衡量）"""
    h, w = hw
    H = np.asarray(H, dtype=np.float64) / H[2, 2]
    corners = np.array([[0, 0, 1], [w, 0, 1], [0, h, 1], [w, h, 1]], dtype=np.float64).T
    p = H @ corners
    pa = np.vstack([H[:2], [0.0, 0.0, 1.0]]) @ corners
    return float(np.max(np.linalg.norm(p[:2] / p[2] - pa[:2] / pa[2], axis=0)))


def _homography_ok(H, hw):
    """全尺寸 3×3 warp 解析门控，返回 (ok, 原因)。

    ECC 只允许解出小残差：旋转/平移与欧氏门控同口径（换算全尺寸），
    透视项用四角投影位移衡量（去透视仿射作对照）。
    """
    h, w = hw
    H = H / H[2, 2]
    M = H[:2, :2]
    det = float(np.linalg.det(M))
    if det <= 0:
        return False, f'翻转(det={det:.3f})'
    rot = abs(np.degrees(np.arctan2(float(M[1, 0]), float(M[0, 0]))))
    if rot > 0.5:
        return False, f'旋转 {rot:.2f}°>0.5°'
    if abs(float(H[0, 2])) > 60 or abs(float(H[1, 2])) > 60:
        return False, f'平移 ({H[0, 2]:.1f},{H[1, 2]:.1f})>60px'
    s = np.linalg.svd(M, compute_uv=False)
    if not (0.97 <= float(s[0]) <= 1.03 and 0.97 <= float(s[1]) <= 1.03):
        return False, f'缩放异常(奇异值 {s[0]:.3f},{s[1]:.3f})'
    disp = _persp_disp(H, (h, w))
    if disp > 0.005 * min(w, h):
        return False, f'透视位移 {disp:.1f}px 超限'
    return True, f'透视 {disp:.2f}px'


def _refine_homography(ref_half, frm_half, dx_full, dy_full, crop_ratio=0.55):
    """半尺寸 ECC 单应精修（平移+旋转+透视）

    欧氏解留不住的透视项（手持绕非光心转动、连拍间 rolling shutter 差异）
    在同一窗口、同一预对齐口径下补齐。窗口提取与 _refine_euclidean 完全一致。
    返回 (H 3x3 float64: ref半尺寸crop -> frm半尺寸crop, fx0, fy0, x0h, y0h)。
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
    warp = np.array([[1.0, 0.0, dx - ix],
                     [0.0, 1.0, dy - iy],
                     [0.0, 0.0, 1.0]], dtype=np.float32)
    _, warp = cv2.findTransformECC(ref_c, frm_c, warp, cv2.MOTION_HOMOGRAPHY,
                                   (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-6),
                                   None, 3)
    return warp.astype(np.float64), fx0, fy0, x0, y0


def _noise_weights(blur_rms, usable, ref_idx):
    """实测残差定权：w = 1/(rms² + floor)，floor = usable 帧 rms² 的中位数。

    相对中位归一后截断到 [0.5, 2]：锐利帧不独大、模糊帧不丢剔（剔除由
    RMS 门限负责，加权只是软修正）。残差缺失的帧权重 1，参考帧取其余帧中位。
    返回 (usable 顺序的权重数组, 参考帧权重)。
    """
    rms = np.array([blur_rms[i] for i in usable], dtype=np.float64)
    fin = np.isfinite(rms)
    if not fin.any():
        return np.ones(len(usable), dtype=np.float32), 1.0
    r2 = rms[fin] ** 2
    floor = float(np.median(r2))
    w = np.ones(len(usable), dtype=np.float64)
    w[fin] = 1.0 / (r2 + floor)
    med = float(np.median(w[fin]))
    w[fin] = np.clip(w[fin] / med, 0.5, 2.0)
    w_ref = float(np.median(w))
    return w.astype(np.float32), w_ref


def _align_stats(ref_half, frm_half, P, gain=1.0, bias=0.0):
    """半尺寸对齐质量：内容运动占比 + 模糊残差 RMS（射影 P，ref->frame）。

    remap 边界填 -1 生成 valid 掩码；RMS 在光度校正后计算，
    避免曝光差被误判为模糊。仿射 P 的齐次项恒为 1.0，除法逐位无损。
    """
    h, w = ref_half.shape
    xs = np.arange(w, dtype=np.float32)
    ys = np.arange(h, dtype=np.float32)
    P32 = np.asarray(P, dtype=np.float32)
    d = P32[2, 0] * xs[None, :] + P32[2, 1] * ys[:, None] + P32[2, 2]
    map_x = (P32[0, 0] * xs[None, :] + P32[0, 1] * ys[:, None] + P32[0, 2]) / d
    map_y = (P32[1, 0] * xs[None, :] + P32[1, 1] * ys[:, None] + P32[1, 2]) / d
    aligned = cv2.remap(frm_half, map_x, map_y, interpolation=cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_CONSTANT, borderValue=-1.0)
    valid = aligned >= 0
    diff = np.abs(aligned - ref_half)
    mf = float(((diff > 0.06) & valid).sum() / max(valid.sum(), 1))
    rms = np.nan
    if valid.sum() > 100:
        aligned_corr = np.clip(aligned * float(gain) + float(bias), 0, 1)
        resid = (aligned_corr - ref_half)[valid]
        rms = float(np.sqrt(np.mean(resid ** 2)))
    return mf, rms


def _local_field(ref_half, ali_half, tile=128, max_local=6.0, min_std=0.02, min_resp=0.10):
    """分块相位相关测局部残差场（视差校正：最近物体帧间分歧可达数像素）。

    ref_half/ali_half 同尺寸半图，ali 为按全局 warp 对齐后的帧（无效区=-1）。
    约定与帧间平移一致：特征在 ref 的 q 处 -> 在 ali 的 q+offset 处；
    融合采样时叠加 2*offset（半尺寸转全尺寸）。平坦块/低响应块/边界缺损块
    留零（退化为纯全局 warp）。
    返回 (offsets (J,K,2) float32 半尺寸单位, valid (J,K) bool)。
    """
    hh, hw = ref_half.shape
    J, K = hh // tile, hw // tile
    win = cv2.createHanningWindow((tile, tile), cv2.CV_32F)
    offsets = np.zeros((J, K, 2), dtype=np.float32)
    valid = np.zeros((J, K), dtype=bool)
    for j in range(J):
        for k in range(K):
            r = ref_half[j * tile:(j + 1) * tile, k * tile:(k + 1) * tile]
            if float(r.std()) < min_std:
                continue
            a = ali_half[j * tile:(j + 1) * tile, k * tile:(k + 1) * tile]
            good = a >= 0
            if int(good.sum()) < tile * tile * 0.6:
                continue
            a = np.where(good, a, 0.0).astype(np.float32)
            (dx, dy), resp = cv2.phaseCorrelate(r * win, a * win)
            if resp < min_resp:
                continue
            offsets[j, k] = (np.clip(dx, -max_local, max_local),
                             np.clip(dy, -max_local, max_local))
            valid[j, k] = True
    return offsets, valid


def _apply_field(off, SX, SY, tile_full=256):
    """融合采样坐标叠加局部偏移场（单元中心双线性，网格外常数外推）。

    off 为半尺寸单位控制格；SX/SY 为全尺寸场景坐标；返回全尺寸偏移 (dx, dy)。
    """
    J, K, _ = off.shape
    gx = SX / tile_full - 0.5
    gy = SY / tile_full - 0.5
    gxf = np.floor(gx)
    gyf = np.floor(gy)
    ix = np.clip(gxf.astype(np.int32), 0, K - 2)
    iy = np.clip(gyf.astype(np.int32), 0, J - 2)
    fx = np.clip(gx - gxf, 0.0, 1.0).astype(np.float32)[..., None]
    fy = np.clip(gy - gyf, 0.0, 1.0).astype(np.float32)[..., None]
    o00 = off[iy, ix]
    o01 = off[iy, ix + 1]
    o10 = off[iy + 1, ix]
    o11 = off[iy + 1, ix + 1]
    out = (o00 + (o01 - o00) * fx) * (1 - fy) + (o10 + (o11 - o10) * fx) * fy
    return (out[..., 0] * np.float32(2.0)).astype(np.float32), \
           (out[..., 1] * np.float32(2.0)).astype(np.float32)


def reconstruct(raw_files, shifts, ref_idx, scale, use_deghost, tile=1024, camera_linear=False,
              blur_gate=True, use_homography=True, use_noise_weight=True,
              use_local_align=True, workers=0):
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
      5. 对齐模型：相位相关 -> ECC 欧氏 -> ECC 单应（解析门控 + 残差验收，
         不达标退回欧氏——任何情况都不差于纯欧氏）
      6. 噪声感知加权：实测残差定权（锐帧多信、糊帧少信），截断 [0.5, 2]
      7. 局部对齐（视差校正）：全局 warp 后分块相位相关精修残差平移，
         最近物体的帧间分歧全局模型吃不掉（实测可达数像素），场修正残差
         须优于全局（≤0.98×）才生效

    camera_linear=True 时帧按线性相机原生色彩空间解码（Linear DNG 用），
    融合结果保持线性、不做任何显示变换。
    """
    n = len(raw_files)

    # 帧缓存：uint16（45MP 一帧 float32 要 540MB，16 帧装不下）
    print("  读入帧缓存 (uint16)...")
    _tc = time.time()
    frames = []
    for f in raw_files:
        img = read_raw_full(f, camera_space=camera_linear)
        frames.append((np.clip(img, 0.0, 1.0) * 65535.0).astype(np.uint16))
        del img
        gc.collect()
    print(f"  [计时] 读帧 {time.time() - _tc:.1f}s")

    # 位移精修（平移+旋转）+ 光度校正参数
    ref_u16 = frames[ref_idx]
    h, w = ref_u16.shape[:2]
    ref_gray = np.power(np.clip(np.mean(ref_u16, axis=2, dtype=np.float32) / 65535.0, 0, 1), 1 / 2.2)
    ref_half = cv2.resize(ref_gray, (ref_gray.shape[1] // 2, ref_gray.shape[0] // 2))
    gains = np.ones(n, dtype=np.float32)
    biases = np.zeros(n, dtype=np.float32)
    warps = [np.eye(3, dtype=np.float64) for _ in range(n)]   # ref->frame 全尺寸
    proj = [False] * n                                        # 该帧是否含透视项
    _ta = time.time()
    motion_frac = np.zeros(n, dtype=np.float32)
    blur_rms = np.full(n, np.nan, dtype=np.float32)
    max_rot = 0.0
    max_persp = 0.0
    S2 = np.diag([2.0, 2.0, 1.0])
    S2i = np.diag([0.5, 0.5, 1.0])
    for i in range(n):
        if i == ref_idx:
            continue
        fg = np.power(np.clip(np.mean(frames[i], axis=2, dtype=np.float32) / 65535.0, 0, 1), 1 / 2.2)
        shifts[i] = _refine_shift(ref_gray, fg, shifts[i])
        gains[i], biases[i] = _estimate_gain_bias(ref_gray, fg, shifts[i])
        fg_half = cv2.resize(fg, (fg.shape[1] // 2, fg.shape[0] // 2))
        # ECC 欧氏精修（半尺寸）: 捕捉帧间旋转，纯平移模型下 0.2° = 边缘 ~15px 错位
        rot = 0.0
        homo_msg = ''
        # 半尺寸 warp（ref半尺寸坐标 -> frame半尺寸坐标），ECC 失败时退回纯平移
        Ph = np.array([[1.0, 0.0, shifts[i][0] / 2.0],
                       [0.0, 1.0, shifts[i][1] / 2.0],
                       [0.0, 0.0, 1.0]], dtype=np.float64)
        try:
            E, fx0, fy0, x0h, y0h = _refine_euclidean(ref_half, fg_half, *shifts[i])
            rot_raw = float(np.degrees(np.arctan2(E[1, 0], E[0, 0])))
            if abs(rot_raw) > 0.5 or abs(E[0, 2]) > 30 or abs(E[1, 2]) > 30:
                # 窗口已按整数平移预对齐，ECC 只应解出亚像素残差+微小旋转
                raise ValueError(f'ECC 解不合理(旋转 {rot_raw:+.2f}°, 平移 {E[0,2]:.1f},{E[1,2]:.1f})，退回纯平移')
            E3 = np.vstack([E, [0.0, 0.0, 1.0]])
            Th = np.array([[1, 0, fx0], [0, 1, fy0], [0, 0, 1]], dtype=np.float64)
            To = np.array([[1, 0, -x0h], [0, 1, -y0h], [0, 0, 1]], dtype=np.float64)
            Ph = Th @ E3 @ To                             # ref半尺寸坐标 -> frame半尺寸坐标
            rot = float(np.degrees(np.arctan2(Ph[1, 0], Ph[0, 0])))
            max_rot = max(max_rot, abs(rot))
            # 单应精修: 欧氏解之上补透视项（绕非光心转动/rolling shutter 差异）。
            # 解析门控 + 残差验收双保险，任何分支都不差于欧氏解。
            if use_homography:
                try:
                    Hc, hfx0, hfy0, hx0, hy0 = _refine_homography(ref_half, fg_half, *shifts[i])
                    PH = (np.array([[1, 0, hfx0], [0, 1, hfy0], [0, 0, 1]], dtype=np.float64)
                          @ Hc
                          @ np.array([[1, 0, -hx0], [0, 1, -hy0], [0, 0, 1]], dtype=np.float64))
                    okH, whyH = _homography_ok(S2 @ PH @ S2i, (h, w))
                    if not okH:
                        homo_msg = f'单应✗({whyH})'
                        raise ValueError(whyH)
                    PH = PH / PH[2, 2]
                    rms_e = _align_stats(ref_half, fg_half, Ph, gains[i], biases[i])[1]
                    rms_h = _align_stats(ref_half, fg_half, PH, gains[i], biases[i])[1]
                    if np.isfinite(rms_h) and rms_h <= 0.98 * rms_e:
                        Ph = PH
                        rot = float(np.degrees(np.arctan2(Ph[1, 0], Ph[0, 0])))
                        max_rot = max(max_rot, abs(rot))
                        proj[i] = abs(Ph[2, 0]) > 1e-7 or abs(Ph[2, 1]) > 1e-7
                        max_persp = max(max_persp, _persp_disp(S2 @ Ph @ S2i, (h, w)))
                        homo_msg = f'单应✓(rms {rms_e:.4f}->{rms_h:.4f})'
                    else:
                        homo_msg = f'单应✗(rms {rms_e:.4f}->{rms_h:.4f})'
                except (cv2.error, ValueError):
                    pass  # 单应不收敛/被拒：保留欧氏解
        except (cv2.error, ValueError):
            pass  # ECC 不收敛/解不合理时退回纯平移（单应随之跳过）
        warps[i] = S2 @ Ph @ S2i                      # 半尺寸 -> 全尺寸
        # 内容运动检查 + 模糊残差 RMS：对齐(含透视)后仍大幅差异 = 内容移动/抖糊。
        # 无论各级 ECC 成败都必须执行（用最终 warp），否则坏帧会漏检。
        motion_frac[i], blur_rms[i] = _align_stats(ref_half, fg_half, Ph,
                                                   gains[i], biases[i])
        extra = f' {homo_msg}' if homo_msg else ''
        print(f"  精修 {raw_files[i].name}: dx={warps[i][0, 2]:+8.2f} dy={warps[i][1, 2]:+8.2f} "
              f"旋转={rot:+7.3f}°{extra} 增益={gains[i]:.4f} 偏置={biases[i]:+.4f} "
              f"运动占比={motion_frac[i]:.1%} 模糊RMS={blur_rms[i]:.4f}")
        del fg, fg_half
    print(f"  [计时] 精修 {time.time() - _ta:.1f}s")
    del ref_gray          # ref_half 留给局部对齐阶段（~10MB）
    gc.collect()

    # 剔除运动帧: 对齐后差异区域超过 15% 的帧整帧不用（人物走动/动物等）。
    # 去鬼影依赖中位数锚定，四帧起才稳（与 sr_suggest.MIN_COMPOSE_FRAMES 一致）。
    usable = [i for i in range(n) if motion_frac[i] <= 0.15]
    for i in range(n):
        if motion_frac[i] > 0.15:
            print(f"  [剔除] {raw_files[i].name}: 运动占比 {motion_frac[i]:.1%}，内容大幅移动")
    # 模糊 RMS 门限: 中位数×1.2 以上的帧整帧剔除（手抖糊帧，对齐无法修复）。
    # 中位数在"运动剔除后的可用帧"上取（参考帧 RMS=0 会拉低中位数）；门限剔除后
    # 若不足 4 帧（合成门槛），按 RMS 从低到高回补至 4 帧。
    rms_pool = [i for i in usable if i != ref_idx and np.isfinite(blur_rms[i])
                and blur_rms[i] > 0]
    if blur_gate and len(rms_pool) >= 3:
        rms_median = float(np.median([blur_rms[i] for i in rms_pool]))
        rms_limit = rms_median * 1.2
        gated = [i for i in usable if not (
            i != ref_idx and np.isfinite(blur_rms[i]) and blur_rms[i] > rms_limit)]
        dropped = [i for i in usable if i not in set(gated)]
        if len(gated) < 4:
            kept_sorted = sorted(rms_pool, key=lambda i: blur_rms[i])
            gated = gated + [i for i in kept_sorted if i not in set(gated)][:4 - len(gated)]
            gated.sort()
        for i in dropped:
            if i not in set(gated):
                print(f"  [剔除] {raw_files[i].name}: 模糊RMS {blur_rms[i]:.4f} > "
                      f"门限 {rms_limit:.4f}（中位 {rms_median:.4f}×1.2），单帧抖糊")
        usable = sorted(set(gated))
    min_need = 4
    if len(usable) < min_need:
        raise ValueError(
            f'画面内容大幅移动（人物/动物等）：有效帧仅 {len(usable)} 张（需≥{min_need}），无法合成。\n'
            '建议: 该场景改用单张最佳帧，或提高快门速度/缩小画面占比重拍。')
    if len(usable) < n:
        gc.collect()

    # 局部对齐（视差校正）：全局 warp 后分块相位相关精修残差平移。
    # 最近物体的帧间分歧全局模型吃不掉（实测可达数像素），场修正残差
    # 须优于全局（≤0.98×）才采纳，任何情况不差于纯全局 warp。
    fields = {}
    _tl = time.time()
    if use_local_align:
        hh, hw = ref_half.shape
        for i in usable:
            if i == ref_idx:
                continue
            fg = np.power(np.clip(np.mean(frames[i], axis=2, dtype=np.float32) / 65535.0, 0, 1), 1 / 2.2)
            fh = cv2.resize(fg, (fg.shape[1] // 2, fg.shape[0] // 2))
            P32 = (S2i @ warps[i] @ S2).astype(np.float32)
            xs = np.arange(hw, dtype=np.float32)
            ys = np.arange(hh, dtype=np.float32)
            den = P32[2, 0] * xs[None, :] + P32[2, 1] * ys[:, None] + P32[2, 2]
            ali = cv2.remap(fh,
                            (P32[0, 0] * xs[None, :] + P32[0, 1] * ys[:, None] + P32[0, 2]) / den,
                            (P32[1, 0] * xs[None, :] + P32[1, 1] * ys[:, None] + P32[1, 2]) / den,
                            interpolation=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=-1.0)
            off, vld = _local_field(ref_half, ali)
            rms_local = np.nan
            if vld.any():
                U, V = np.meshgrid(np.arange(hw, dtype=np.float32), np.arange(hh, dtype=np.float32))
                dxf, dyf = _apply_field(off, U * 2, V * 2)
                corr = cv2.remap(ali, U + dxf / 2, V + dyf / 2, interpolation=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=-1.0)
                good = corr >= 0
                if int(good.sum()) > 100:
                    cc = np.clip(corr * float(gains[i]) + float(biases[i]), 0, 1)
                    rms_local = float(np.sqrt(np.mean(((cc - ref_half)[good]) ** 2)))
            del fg, fh, ali
            if (not vld.any() or not np.isfinite(rms_local)
                    or not np.isfinite(blur_rms[i]) or rms_local > 0.98 * blur_rms[i]):
                continue
            fields[i] = off
            print(f"  局部对齐 {raw_files[i].name}: {int(vld.sum())}块有效, "
                  f"中位|F|={float(np.median(np.abs(off[vld]))):.2f}px(半), "
                  f"rms {blur_rms[i]:.4f}->{rms_local:.4f}")
            blur_rms[i] = rms_local
        if fields:
            print(f"  局部对齐生效帧: {','.join(raw_files[i].stem for i in fields)}")
            gc.collect()
        print(f"  [计时] 局部对齐 {time.time() - _tl:.1f}s")

    gains_u = gains[usable]
    biases_u = biases[usable]
    # 噪声感知加权：实测残差定权（K14 显式噪声参数的等效实现，不需要机型噪声表）
    w_us = None
    if use_noise_weight and len(usable) >= 2:
        w_arr, w_ref = _noise_weights(blur_rms, usable, ref_idx)
        if np.any(w_arr != np.float32(1.0)) or w_ref != 1.0:
            w_arr[list(usable).index(ref_idx)] = w_ref
            w_us = w_arr
            wtxt = ' '.join(f'{raw_files[i].stem}×{w:.2f}' for i, w in zip(usable, w_arr))
            print(f"  噪声加权: {wtxt}")

    # 重叠画布：场景点 s 需对每帧都满足 s + d ∈ [0, w)；旋转按内切矩形收缩画布，
    # 透视帧按角点位移留保守余量（上限 50px）
    min_dx, max_dx = min(s[0] for s in shifts), max(s[0] for s in shifts)
    min_dy, max_dy = min(s[1] for s in shifts), max(s[1] for s in shifts)
    rot_mx = int(np.ceil(np.tan(np.radians(max_rot)) * h / 2)) if max_rot > 0.05 else 0
    rot_my = int(np.ceil(np.tan(np.radians(max_rot)) * w / 2)) if max_rot > 0.05 else 0
    if max_persp > 0.01:
        per_m = min(int(np.ceil(max_persp)), 50)
        rot_mx += per_m
        rot_my += per_m
    if fields:
        max_f = max(float(np.max(np.abs(o))) for o in fields.values())
        loc_m = min(int(np.ceil(max_f * 2)), 30)   # 偏移场最大位移（半->全尺寸）
        rot_mx += loc_m
        rot_my += loc_m
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

    # 分块鲁棒融合（块间独立——多线程并行；remap/GaussianBlur 释放 GIL）
    out = np.zeros((sr_h, sr_w, 3), dtype=np.uint16)
    sx_base = (np.arange(sr_w, dtype=np.float32) + 0.5) / scale - 0.5 + crop_x0
    sy_base = (np.arange(sr_h, dtype=np.float32) + 0.5) / scale - 0.5 + crop_y0
    robust = use_deghost and n >= 3
    any_proj = any(proj[i] for i in usable)
    _tf = time.time()
    if any_proj:
        names = ','.join(raw_files[i].stem for i in usable if proj[i])
        print(f"  透视warp生效帧: {names}")
    tile_jobs = [(ty, tx) for ty in range(0, sr_h, tile) for tx in range(0, sr_w, tile)]
    nw = _worker_count(workers)

    def fuse_tile(ty, tx):
        th = min(tile, sr_h - ty)
        tw = min(tile, sr_w - tx)
        SX, SY = np.meshgrid(sx_base[tx:tx + tw], sy_base[ty:ty + th])
        S = np.empty((len(usable), th, tw, 3), dtype=np.float32)
        for j, i in enumerate(usable):
            if any_proj:
                mx, my = _proj_coords(warps[i], SX, SY)
            else:
                # 纯平移/旋转组：仿射快路径，与局部对齐引入前的输出逐位一致
                A = warps[i][:2].astype(np.float32)
                mx = (A[0, 0] * SX + A[0, 1] * SY + A[0, 2]).astype(np.float32)
                my = (A[1, 0] * SX + A[1, 1] * SY + A[1, 2]).astype(np.float32)
            if i in fields:
                dxf, dyf = _apply_field(fields[i], SX, SY)
                mx = mx + dxf
                my = my + dyf
            S[j] = cv2.remap(frames[i], mx.astype(np.float32), my.astype(np.float32),
                             interpolation=cv2.INTER_CUBIC,
                             borderMode=cv2.BORDER_REPLICATE)
        S -= biases_u[:, None, None, None]
        S *= gains_u[:, None, None, None]
        if robust:
            med = np.median(S, axis=0)
            rel = np.abs(S - med[None]).mean(axis=3) / (1310.0 + med.mean(axis=2))
            wmat = 1.0 / (1.0 + (rel / 0.25) ** 2)
            if w_us is not None:
                wmat = wmat * w_us[:, None, None]
            val = (S * wmat[..., None]).sum(axis=0) / np.maximum(wmat.sum(axis=0), 1e-6)[..., None]
        elif w_us is not None:
            val = (S * w_us[:, None, None, None]).sum(axis=0) / float(w_us.sum())
        else:
            val = S.mean(axis=0)
        return ty, tx, np.clip(val + 0.5, 0, 65535).astype(np.uint16)

    cv_prev = cv2.getNumThreads()
    done = 0
    if nw > 1:
        cv2.setNumThreads(1)   # 块间并行，块内 cv2 不再叠线程（避免超订）
        try:
            with ThreadPoolExecutor(max_workers=nw) as ex:
                for ty, tx, blk in ex.map(lambda t: fuse_tile(t[0], t[1]), tile_jobs):
                    out[ty:ty + min(tile, sr_h - ty), tx:tx + min(tile, sr_w - tx)] = blk
                    done += 1
                    print(f"\r  融合: {done}/{len(tile_jobs)} 块", end="", flush=True)
        finally:
            cv2.setNumThreads(cv_prev)
    else:
        for ty, tx in tile_jobs:
            rty, rtx, blk = fuse_tile(ty, tx)
            out[rty:rty + min(tile, sr_h - rty), rtx:rtx + min(tile, sr_w - rtx)] = blk
            done += 1
            print(f"\r  融合: {done}/{len(tile_jobs)} 块", end="", flush=True)
    print()
    print(f"  [计时] 融合 {time.time() - _tf:.1f}s")
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


# ===== Jiangtherapee Fast K14 外挂引擎（闭源，仅本机授权使用；商用/分发前须确认许可）=====

K14_MAX_FRAMES = 14          # K14 = 14 帧神经融合
K14_READ_NOISE_DEFAULT = 2.6 # e⁻，Z6 ISO800 量级（Z6 不在其 PTC 表，满阱用其默认 20000）


def run_k14_engine(backend_exe, frame_paths, ref_path, out_dir, out_prefix='k14',
                   black=1008, read_noise=K14_READ_NOISE_DEFAULT, fullwell=20000,
                   timeout=1200):
    """调用 Jiangtherapee Bridge 的 jsr_fast_k14_backend.exe（WGPU 神经重建）

    CLI 坑（2.3.27 实测）：--inputs 是 **JSON 数组文件**（read_text 后 json.loads）；
    --reference 是帧索引（int）；DNG 导出强制 LibRaw 矩阵（勿传 camera-model/
    profile-source）；必须显式喂 --black/--read-noise/--fullwell。
    black=None 时从参考帧 rawpy 元数据自动取。返回 (dng_path 或 None, 耗时秒)。
    """
    import json
    import subprocess
    import time
    import uuid
    frame_paths = [Path(p) for p in frame_paths]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if black is None:
        import rawpy
        with rawpy.imread(str(ref_path)) as raw:
            black = int(np.median(np.asarray(raw.black_level_per_channel, np.float64)))
    lst = out_dir / f'_k14_inputs_{uuid.uuid4().hex[:8]}.json'
    lst.write_text(json.dumps([str(p) for p in frame_paths]), encoding='utf-8')
    cmd = [str(backend_exe),
           '--inputs', str(lst),
           '--reference', str(frame_paths.index(Path(ref_path))),
           '--black', str(int(black)),
           '--read-noise', str(float(read_noise)),
           '--fullwell', str(float(fullwell)),
           '--output-dir', str(out_dir),
           '--out-prefix', out_prefix,
           '--out-dng', 'on', '--out-format', 'jpeg', '--jpeg-quality', '95']
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           cwd=str(Path(backend_exe).parent))
        if r.returncode != 0:
            print(f"  [K14] 后端退出码 {r.returncode}:")
            print("  [K14][out] " + r.stdout.decode("utf-8", "replace")[-1500:])
            print("  [K14][err] " + r.stderr.decode("utf-8", "replace")[-800:])
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  [K14] 调用失败: {e}")
        return None, time.time() - t0
    finally:
        lst.unlink(missing_ok=True)
    dng = out_dir / f'{out_prefix}.dng'
    meta_path = out_dir / 'RESULT.json'
    ok = dng.exists()
    try:
        ok = ok and json.loads(meta_path.read_text(encoding='utf-8')).get('status') == 'COMPLETE'
    except Exception:
        pass
    return (dng if ok else None), time.time() - t0


def load_subifd_rgb(dng_path):
    """读 Adobe 结构 DNG 的 SubIFD（LinearRaw）为 RGB u16（tifffile 官方通道，
    兼容无损 JPEG 压缩的 SubIFD——手写瓦片解析器不支持压缩）"""
    import tifffile
    tf = tifffile.TiffFile(str(dng_path))
    for s in tf.series:
        if s.shape[0] > 1000 and s.dtype == np.uint16:   # 主 raw 序列
            return s.asarray()
    raise ValueError(f'{dng_path} 未找到 LinearRaw 序列')


def process(input_folder=None, output_path=None, scale=2, max_frames=16, max_shift=150.0,
            use_deghost=True, sharpen=0.3, use_denoise=False, denoise_strength=5,
            use_legacy=False, tile=1024, threads=0, master_scale=None, output_dng=None,
            rl_sharpen=True, blur_gate=True, k14_backend=None,
            use_homography=True, use_noise_weight=True, use_local_align=True,
            files=None):
    """output_dng：True/False 显式控制；None=自动（全 RAW 组 + ExifTool 可用则 DNG）。

    DNG 模式：线性相机域融合，输出 Linear DNG 母版 + sRGB JPG 预览，
    不做锐化/降噪/gamma/自动拉伸（调色全部留给 LR，白平衡可调 Kelvin）。
    JPG 组或 ExifTool 缺失时回退 sRGB TIF+JPG。

    files：显式文件清单（Path/str 列表）。给出时跳过 input_folder 扫描——
    GUI 按组直接传清单，不再拷贝临时文件夹。
    """
    if threads and threads > 0:
        # 限制计算线程数（如只用 P 核 8 线程，控制功耗温度；0 = 全部核心）
        cv2.setNumThreads(int(threads))
    print("=" * 60)
    print("手持多帧超分辨率合成")
    print("=" * 60)

    t0 = time.time()

    raw_files = [Path(p) for p in files] if files else find_input_files(input_folder)
    n = len(raw_files)
    print(f"  文件数: {n}")

    # 找最佳参考帧 + 对齐
    print("\n[1/4] 找最佳参考帧并对齐...")
    _t1 = time.time()
    ref_idx = find_best_ref_frame(raw_files, half_size=True)
    shifts = align_all_frames(raw_files, ref_idx, half_size=True)
    print(f"  [计时] 预对齐 {time.time() - _t1:.1f}s")

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
    all_frames = list(raw_files)          # K14 等外挂引擎可能需要从全组补帧
    selected_indices, raw_files, shifts, ref_idx = select_best_frames(
        raw_files, shifts, ref_idx, max_frames, max_shift
    )
    print(f"  使用 {len(raw_files)} 帧, 参考帧: {ref_idx}")

    # DNG 可用性判定（需在重建前定：决定解码色彩域）
    et = None
    try:
        from exif_reader import find_exiftool
        et = find_exiftool()
    except Exception:
        et = None
    dng_meta = None
    as_shot_neutral = None
    use_dng = False
    if output_dng is not False and et and not use_legacy:
        if all(Path(str(f)).suffix.lower() in RAW_EXTS for f in raw_files):
            dng_meta = extract_dng_metadata(raw_files[ref_idx])
            if dng_meta:
                # 实测优先（与存储数据自洽：nocam 已除回 daylight，实测≈1/cam_mul）
                as_shot_neutral = measure_as_shot_neutral(raw_files[ref_idx])
                if as_shot_neutral is None:
                    import rawpy as _rp
                    with _rp.imread(str(raw_files[ref_idx])) as _raw:
                        as_shot_neutral = neutral_from_cam_mul(_raw.camera_whitebalance)
                use_dng = as_shot_neutral is not None
    if k14_backend and use_dng and scale == 2:
        # ===== K14 外挂引擎流程：分组/选帧/EXIF/交付归我们，对齐重建交给 K14 =====
        import shutil as _shutil
        base = str(output_path).rsplit('.', 1)[0]
        ref_frame = raw_files[ref_idx]
        # K14 恰好吃 14 帧：从全组按高频能量（清晰度）排序取前 14，参考帧强制保留。
        # 内置选帧滤掉的大位移帧也参与排名——K14 自带对齐（Fast 大位移），给它们机会。
        scored = []
        for f in all_frames:
            g = read_raw_as_gray(f, half_size=True)
            scored.append((float((g - cv2.GaussianBlur(g, (0, 0), 2.0)).std()), f))
        scored.sort(key=lambda x: -x[0])
        frames14 = [f for _, f in scored[:K14_MAX_FRAMES]]
        if ref_frame not in frames14:
            frames14[-1] = ref_frame
        print(f"\n[3/4] K14 外挂引擎（{len(frames14)} 帧, 参考 {ref_frame.name}）...")
        work = Path(base + '_k14work')
        dng14, secs = run_k14_engine(k14_backend, frames14, ref_frame, work)
        if dng14 is None:
            raise RuntimeError("K14 引擎失败（见上方日志；确认 Bridge resources 目录与显卡 WGPU 可用）")
        final = base + '_2x.dng'
        _shutil.move(str(dng14), final)
        big = load_subifd_rgb(final)
        out_h, out_w = big.shape[:2]
        if copy_exif_from_frame(et, ref_frame, final, width=out_w, height=out_h):
            print(f"  DNG:  {final} ({Path(final).stat().st_size/(1024*1024):.0f}MB, K14 线性 DNG, 完整EXIF已继承)")
        else:
            print(f"  DNG:  {final} ({Path(final).stat().st_size/(1024*1024):.0f}MB, K14 线性 DNG)")
        transform = derive_camera_to_srgb(ref_frame)
        if transform is None:
            transform = (np.eye(3), np.zeros(3))
        jpg_path = base + f'_{scale}x.jpg'
        print("  JPG:  RL 反卷积锐化（12 iter, σ=1.0）...")
        big_rl = rl_deconvolve_u16(big, workers=threads)
        prev = render_srgb_preview(big_rl.astype(np.float32) / 65535.0, transform, stretch=True)
        del big, big_rl
        ok, jpg_buf = cv2.imencode('.jpg', cv2.cvtColor(prev, cv2.COLOR_RGB2BGR),
                                   [cv2.IMWRITE_JPEG_QUALITY, 90])
        if ok:
            Path(jpg_path).write_bytes(jpg_buf.tobytes())
            copy_exif_from_frame(et, ref_frame, jpg_path, width=out_w, height=out_h)
        print(f"  JPG:  {jpg_path} ({Path(jpg_path).stat().st_size/(1024*1024):.1f}MB, sRGB 预览)")
        _shutil.rmtree(work, ignore_errors=True)
        elapsed = time.time() - t0
        print(f"\n{'=' * 60}")
        print(f"完成！ {len(frames14)} 帧 K14 外挂引擎（内核耗时 {secs:.0f} 秒）")
        print(f"耗时: {elapsed:.1f} 秒")
        return

    if output_dng and not use_dng and not use_legacy:
        print("  提示: DNG 母版不可用（JPG 组 / 缺 ExifTool / 元数据异常），输出 sRGB TIF")

    # 超分重建
    print(f"\n[3/4] 超分辨率重建 (x{scale}, "
          f"{'旧版对齐融合' if use_legacy else 'SR网格鲁棒融合'}"
          f"{', 线性相机域' if use_dng else ''})...")
    _t3 = time.time()
    if use_legacy:
        sr_result, (orig_w, orig_h) = reconstruct_legacy(raw_files, shifts, ref_idx, scale, use_deghost)
    else:
        sr_result, (orig_w, orig_h) = reconstruct(raw_files, shifts, ref_idx, scale, use_deghost,
                                                  tile, camera_linear=use_dng, blur_gate=blur_gate,
                                                  use_homography=use_homography,
                                                  use_noise_weight=use_noise_weight,
                                                  use_local_align=use_local_align,
                                                  workers=threads)
    print(f"  [计时] 重建合计 {time.time() - _t3:.1f}s")

    base = str(output_path).rsplit('.', 1)[0]
    ref_frame = raw_files[ref_idx]

    if use_dng:
        # [4/4] Linear DNG 母版：不做锐化/降噪/gamma/自动拉伸，白平衡与调色留给 LR
        print("\n[4/4] 输出 Linear DNG 母版（线性数据，调色/降噪/锐化全部留给 LR）...")
        out_h, out_w = sr_result.shape[:2]
        arr_u16 = (np.clip(sr_result, 0, 1) * 65535).astype(np.uint16)
        # 预览先渲染（同帧实测仿射 nocam->线性sRGB，含 as-shot WB）：DNG 内嵌
        # LR 默认渲染口径（不拉伸，Windows 图片看内嵌预览才不会与 LR 色彩不一致）；
        # JPG 用旧管观感口径（拉伸提亮，独立交付物）
        transform = derive_camera_to_srgb(ref_frame)
        if transform is None:
            transform = (np.eye(3), np.zeros(3))  # 极端兜底（仅观感退化，不阻断输出）
        preview_master = render_srgb_preview(sr_result, transform, stretch=False)
        dng_path = base + f'_{scale}x.dng'
        if not write_linear_dng(dng_path, arr_u16, dng_meta['matrix'], as_shot_neutral,
                                dng_meta['model'], et,
                                preview_rgb=preview_master, orig_raw_name=ref_frame.name):
            raise RuntimeError(f"DNG 写入失败: {dng_path}")
        dng_mb = Path(dng_path).stat().st_size / (1024*1024)
        if copy_exif_from_frame(et, ref_frame, dng_path, width=out_w, height=out_h):
            print(f"  DNG:  {dng_path} ({dng_mb:.0f}MB, Linear DNG 母版, 完整EXIF已继承)")
        else:
            print(f"  DNG:  {dng_path} ({dng_mb:.0f}MB, Linear DNG 母版)")

        jpg_path = base + f'_{scale}x.jpg'
        if rl_sharpen:
            print("  JPG:  RL 反卷积锐化（12 iter, σ=1.0）...")
            preview_jpg_src = rl_deconvolve_u16(
                (np.clip(sr_result, 0, 1) * 65535).astype(np.uint16),
                workers=threads).astype(np.float32) / 65535.0
        else:
            preview_jpg_src = sr_result
        preview = render_srgb_preview(preview_jpg_src, transform, stretch=True)
        ok, jpg_buf = cv2.imencode('.jpg', cv2.cvtColor(preview, cv2.COLOR_RGB2BGR),
                                   [cv2.IMWRITE_JPEG_QUALITY, 90])
        if ok:
            Path(jpg_path).write_bytes(jpg_buf.tobytes())
            copy_exif_from_frame(et, ref_frame, jpg_path, width=out_w, height=out_h)
        jpg_mb = Path(jpg_path).stat().st_size / (1024*1024)
        print(f"  JPG:  {jpg_path} ({jpg_mb:.0f}MB, sRGB 预览)")

        elapsed = time.time() - t0
        print(f"\n{'=' * 60}")
        print(f"完成！ {orig_w}x{orig_h} → {out_w}x{out_h} (Linear DNG)")
        print(f"耗时: {elapsed:.1f} 秒")
        print(f"{'=' * 60}")
        return

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

    # 完整 EXIF 继承：优先用 ExifTool 把参考帧 EXIF 整体拷到输出（含镜头型号，
    # Lightroom 据此自动挂镜头矫正配置；连拍各帧 EXIF 一致，取参考帧即可）。
    # ExifTool 不可用/失败时回退：JPG 走 piexif 基础字段，TIFF 保持 tifffile extratags。
    # （et / ref_frame 已在 DNG 判定阶段就绪）
    out_h, out_w = sr_result.shape[:2]

    # 保存调色母版与 JPG（母版可选缩放 TIF，LR/PS 可完整调色；JPG 全尺寸分享用）
    exif_tags = build_tiff_exif_tags(exif_data)
    if master_scale and float(master_scale) < float(scale):
        k = float(master_scale) / float(scale)
        mh, mw = round(sr_result.shape[0] * k), round(sr_result.shape[1] * k)
        master = cv2.resize(sr_result, (mw, mh), interpolation=cv2.INTER_AREA)
        master = (np.clip(master, 0, 1) * 65535).astype(np.uint16)
        master_path = f'{base}_{master_scale}x.tif'
        # zlib 无损压缩: 1.5x 母版约 250-350MB（全尺寸 2x 无压缩要 1GB）
        tifffile.imwrite(master_path, master, extratags=exif_tags, compression='zlib')
        if et and copy_exif_from_frame(et, ref_frame, master_path, width=mw, height=mh):
            print(f"  调色母版 TIF: {master_path} ({mw}x{mh}, "
                  f"{Path(master_path).stat().st_size/(1024*1024):.0f}MB, 完整EXIF已继承)")
        else:
            print(f"  调色母版 TIF: {master_path} ({mw}x{mh}, "
                  f"{Path(master_path).stat().st_size/(1024*1024):.0f}MB)")
    else:
        # tifffile 按数组通道顺序原样写入，必须保持 RGB（不能转 BGR，否则红蓝互换）
        tiff_path = base + '.tif'
        img_tiff = (np.clip(sr_result, 0, 1) * 65535).astype(np.uint16)
        tifffile.imwrite(tiff_path, img_tiff, extratags=exif_tags)
        tiff_size = Path(tiff_path).stat().st_size / (1024*1024)
        if et and copy_exif_from_frame(et, ref_frame, tiff_path, width=out_w, height=out_h):
            print(f"  TIFF: {tiff_path} ({tiff_size:.0f}MB, 完整EXIF已继承)")
        else:
            print(f"  TIFF: {tiff_path} ({tiff_size:.0f}MB)")

    # 保存 JPG（预览用）
    jpg_path = base + f'_{scale}x.jpg'
    img_jpg = (np.clip(sr_result, 0, 1) * 255).astype(np.uint8)
    img_jpg = cv2.cvtColor(img_jpg, cv2.COLOR_RGB2BGR)
    # cv2.imwrite 对非 ASCII 路径会静默失败，用 imencode + 直接写文件
    ok, jpg_buf = cv2.imencode('.jpg', img_jpg, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if ok:
        Path(jpg_path).write_bytes(jpg_buf.tobytes())
    else:
        print(f"  警告: JPG 编码失败")

    # 复制 EXIF 到 JPG：优先参考帧完整字段，回退 piexif 基础字段
    if et and copy_exif_from_frame(et, ref_frame, jpg_path, width=out_w, height=out_h):
        pass
    elif exif_bytes:
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
    parser.add_argument("--max-shift", type=float, default=150.0, help="最大允许位移(像素)")
    parser.add_argument("--no-deghost", action="store_true", help="禁用去鬼影")
    parser.add_argument("--sharpen", type=float, default=0.3, help="锐化强度")
    parser.add_argument("--denoise", action="store_true", help="启用降噪")
    parser.add_argument("--legacy", action="store_true", help="使用旧版算法（对齐回参考帧再放大）")
    parser.add_argument("--tile", type=int, default=1024, help="融合分块大小（像素）")
    parser.add_argument("--no-rl-sharpen", action="store_true", help="关闭 JPG 的 RL 反卷积锐化")
    parser.add_argument("--no-blur-gate", action="store_true", help="关闭模糊 RMS 选帧门限")
    parser.add_argument("--no-homography", action="store_true", help="关闭单应精修（退回欧氏对齐）")
    parser.add_argument("--no-noise-weight", action="store_true", help="关闭噪声感知融合加权")
    parser.add_argument("--no-local-align", action="store_true", help="关闭局部对齐（视差校正）")
    parser.add_argument("--k14-backend", default=None,
                        help="Jiangtherapee Fast K14 后端 exe 路径（外挂引擎，2x 专用）")

    args = parser.parse_args()
    process(
        args.input, args.output, args.scale, args.max_frames, args.max_shift,
        not args.no_deghost, args.sharpen, args.denoise, 5, args.legacy, args.tile,
        rl_sharpen=not args.no_rl_sharpen, blur_gate=not args.no_blur_gate,
        k14_backend=args.k14_backend,
        use_homography=not args.no_homography,
        use_noise_weight=not args.no_noise_weight,
        use_local_align=not args.no_local_align
    )


if __name__ == "__main__":
    main()
