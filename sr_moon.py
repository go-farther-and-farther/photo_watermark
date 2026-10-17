# -*- coding: utf-8 -*-
"""小主体 ROI 堆栈（月亮 / 行星 / 日面：暗背景上的亮小目标）

通用超分管线针对手持风景连拍设计，全部假设都建立在"整帧都有内容"上：

- 相位相关 / ECC / 单应 / 噪声权重 / 增益偏置都在整帧上估计，而月亮画面
  99% 是黑天空，这些估计要么被噪声主导、要么被黑背景拉平；
- 重叠画布按全帧位移取交集，而主体只占几百像素——11330x7446 的母版里有效
  内容只有 316px 的月盘，其余全是黑；
- 预览按全帧 P99.5 定白点，黑天空占多数时白点落在天空上，主体被拉顶（白饼）。

本模块按"主体 ROI"重做这条链路：

    detect_bright_subject   检测暗背景上的亮小目标（面积占比/对比度/圆度）
    register_roi            ROI 内亚像素配准：相位相关粗解 + 梯度高斯-牛顿平移
                            精修 + 受验收的 ECC 欧氏精修（吃掉重新取景的滚转）
    estimate_psf_sigma      由盘缘 ESF 的 10-90% 宽度反推 PSF σ
    select_lucky            按盘缘宽度选帧定权（糊帧不参与平均）
    fuse_rois               细网格（scale 倍）逐帧 remap 融合 + 光度归一
    subject_tonemap         以主体中位为锚的显示映射（不把黑背景拉起来）

几何模型是"平移 + 小角度旋转"：月亮/行星在固定三脚架上只有平移，
重新取景会带 0.1~0.5° 画面滚转（本组实测 −0.09°/+0.31°），纯平移配准
在 316px 月盘边缘就是 0.8px 错位。
"""
import numpy as np
import cv2

# 主体判定：面积占比上限、与背景的对比度下限、连通块圆度下限
SUBJECT_MAX_AREA_FRAC = 0.06
SUBJECT_MIN_CONTRAST = 15.0
SUBJECT_MIN_ROUNDNESS = 0.55
# 暗背景判定：全帧 90 分位 < 峰值 × 该比例
DARK_P90_RATIO = 0.10


def detect_bright_subject(gray, max_area_frac=SUBJECT_MAX_AREA_FRAC,
                          min_contrast=SUBJECT_MIN_CONTRAST,
                          min_roundness=SUBJECT_MIN_ROUNDNESS):
    """在灰度图上找"暗背景 + 紧凑亮小目标"（月亮/行星/日面）

    gray: 线性 0..1 float32/float64，2D
    返回 dict(cx, cy, r, area, frac, contrast, bbox=(x0,y0,x1,y1), roundness)
    或 None（不是这类场景）。坐标是 gray 的像素坐标。
    """
    g = np.asarray(gray, dtype=np.float32)
    if g.ndim != 2:
        raise ValueError('detect_bright_subject 需要 2D 灰度图')
    h, w = g.shape
    bg = float(np.median(g))
    peak = float(np.percentile(g, 99.95))
    if not np.isfinite(peak) or peak <= 0:
        return None
    # 暗背景：90 分位远低于峰值（月亮：P90≈背景，峰值=月盘）
    p90 = float(np.percentile(g, 90))
    if p90 > peak * DARK_P90_RATIO:
        return None
    if peak < max(min_contrast * max(bg, 1e-6), 1e-4):
        return None
    thr = max(bg * 4.0, peak * 0.10)
    mask = (g > thr).astype(np.uint8)
    if mask.sum() == 0:
        return None
    n, lab, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        return None
    best = None
    for k in range(1, n):
        area = int(stats[k, cv2.CC_STAT_AREA])
        frac = area / float(h * w)
        if frac > max_area_frac or area < 64:
            continue
        if best is None or area > best[0]:
            best = (area, k)
    if best is None:
        return None
    area, k = best
    x0 = int(stats[k, cv2.CC_STAT_LEFT]); y0 = int(stats[k, cv2.CC_STAT_TOP])
    bw = int(stats[k, cv2.CC_STAT_WIDTH]); bh = int(stats[k, cv2.CC_STAT_HEIGHT])
    cx, cy = float(cents[k][0]), float(cents[k][1])
    cnts, _ = cv2.findContours((lab == k).astype(np.uint8), cv2.RETR_EXTERNAL,
                               cv2.CHAIN_APPROX_NONE)
    (_, _), r_enc = cv2.minEnclosingCircle(cnts[0]) if cnts else ((0, 0), 0.0)
    r_enc = max(float(r_enc), 1.0)
    roundness = float(area / (np.pi * r_enc ** 2))
    r = max(float(np.sqrt(area / np.pi)), 0.5 * float(max(bw, bh)))
    if roundness < min_roundness:
        return None
    # 对比度：盘内中位 / 背景
    yy, xx = np.mgrid[max(0, y0):min(h, y0 + bh), max(0, x0):min(w, x0 + bw)]
    m = ((xx - cx) ** 2 + (yy - cy) ** 2) <= (0.75 * r) ** 2
    if m.sum() < 32:
        return None
    sub = g[yy[m], xx[m]]
    contrast = float(np.median(sub) / max(bg, 1e-6))
    return dict(cx=cx, cy=cy, r=r, area=area, frac=float(area) / (h * w),
                contrast=contrast, bbox=(x0, y0, x0 + bw, y0 + bh),
                roundness=float(roundness), bg=bg, peak=peak)


def _phase_shift(a, b, up=1):
    """b 相对 a 的亚像素平移 (dx, dy)：满足 b(x + d) ≈ a(x)

    只用原生分辨率（上采样域相位相关的峰值会被插值带宽带偏，实测 up=2/4
    反而给出错误解），精度 ~0.05-0.1px，足够做 LK 精修的粗解。
    """
    win = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    (dx, dy), resp = cv2.phaseCorrelate(np.ascontiguousarray(a * win, np.float32),
                                        np.ascontiguousarray(b * win, np.float32))
    return float(dx), float(dy), float(resp)


def _warp_residual(ref, img, M, mask):
    iw = cv2.warpAffine(img, M.astype(np.float32), (img.shape[1], img.shape[0]),
                        flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP,
                        borderMode=cv2.BORDER_REFLECT)
    return float(np.sqrt(np.mean((ref[mask] - iw[mask]) ** 2)))


def register_roi(ref, img, mask=None, max_shift=None, allow_rotation=True,
                 rotations=(-2.0, 2.0)):
    """ROI 配准：ref(x) ≈ img(M · x)，M 为 2x3 仿射（ref 坐标 -> img 坐标）

    1. 相位相关粗解（原生分辨率，~0.1px）
    2. 梯度加权高斯-牛顿精修平移（~0.01px）
    3. 允许旋转时用 ECC 欧氏精修（三脚架重新取景会带 0.1~0.5° 画面滚转，
       0.3° 在 320px 月盘边缘就是 0.84px 错位，纯平移配准吃不掉）
       验收制：残差须 ≤0.98×平移解，且解算出的角度在 rotations 范围内，
       否则退回纯平移——任何情况不差于平移模型。

    返回 (M, 残差RMS, info)
    """
    ref = np.ascontiguousarray(ref, np.float32)
    img = np.ascontiguousarray(img, np.float32)
    if ref.shape != img.shape:
        raise ValueError(f'ROI 尺寸不一致: {ref.shape} vs {img.shape}')
    h, w = ref.shape
    dx, dy, _resp = _phase_shift(ref, img)
    d = np.array([dx, dy], np.float64)
    X, Y = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    if mask is None:
        mask = ref > (0.02 * float(ref.max()) if ref.max() > 0 else 0)
    mask = mask.astype(bool).copy()
    # 去掉 4px 边界（remap 边缘不可靠）
    mask[:4, :] = mask[-4:, :] = False
    mask[:, :4] = mask[:, -4:] = False
    rms = np.inf
    for _ in range(10):
        iw = cv2.remap(img, X + np.float32(d[0]), Y + np.float32(d[1]),
                       interpolation=cv2.INTER_CUBIC,
                       borderMode=cv2.BORDER_REFLECT)
        gy, gx = np.gradient(iw)
        r = (ref - iw)[mask]
        Gx, Gy = gx[mask], gy[mask]
        a11 = float((Gx * Gx).sum()); a12 = float((Gx * Gy).sum()); a22 = float((Gy * Gy).sum())
        b1 = float((Gx * r).sum()); b2 = float((Gy * r).sum())
        det = a11 * a22 - a12 * a12
        if abs(det) < 1e-12:
            break
        delta = np.array([(a22 * b1 - a12 * b2) / det, (a11 * b2 - a12 * b1) / det])
        nrm = float(np.hypot(*delta))
        if nrm > 1.0:   # 单步限幅，防止黑背景噪声把解带飞
            delta *= 1.0 / nrm
        d += delta
        rms = float(np.sqrt(np.mean(r ** 2)))
        if nrm < 1e-4:
            break
    M = np.array([[1.0, 0.0, d[0]], [0.0, 1.0, d[1]]], np.float64)
    info = dict(rot_deg=0.0, rms_trans=rms, rms=rms, rotated=False)
    if max_shift is not None and np.hypot(*d) > max_shift:
        raise ValueError(f'配准位移 {d} 超过上限 {max_shift}px')
    if not allow_rotation:
        return M, rms, info
    # ECC 欧氏精修（在平移解基础上补旋转）
    m8 = mask.astype(np.uint8)
    warp0 = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], np.float32)
    try:
        cc, W = cv2.findTransformECC(
            ref, cv2.remap(img, X + np.float32(d[0]), Y + np.float32(d[1]),
                           interpolation=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT),
            warp0, cv2.MOTION_EUCLIDEAN,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5),
            m8, 5)
    except cv2.error:
        return M, rms, info
    angle = float(np.degrees(np.arctan2(W[1, 0], W[0, 0])))
    if not np.isfinite(angle) or not (rotations[0] <= angle <= rotations[1]):
        return M, rms, info
    # W: ref -> iw；iw(x)=img(T x)  =>  合并 M = T ∘ W
    T3 = np.array([[1.0, 0.0, d[0]], [0.0, 1.0, d[1]], [0.0, 0.0, 1.0]])
    W3 = np.vstack([W.astype(np.float64), [0.0, 0.0, 1.0]])
    M2 = (T3 @ W3)[:2]
    rms2 = _warp_residual(ref, img, M2, mask)
    info.update(rot_deg=angle, rms=rms2, cc=float(cc))
    if np.isfinite(rms2) and rms2 <= 0.98 * rms:
        info['rotated'] = True
        if max_shift is not None and np.hypot(M2[0, 2], M2[1, 2]) > max_shift:
            return M, rms, info
        return M2, rms2, info
    return M, rms, info


def radial_esf(g, cx, cy, r, step=0.02, rmax=1.25):
    """径向平均亮度曲线 (rs, prof)，rs 以 r 为单位"""
    h, w = g.shape
    y0, y1 = max(0, int(cy - r * rmax)), min(h, int(cy + r * rmax) + 1)
    x0, x1 = max(0, int(cx - r * rmax)), min(w, int(cx + r * rmax) + 1)
    sub = g[y0:y1, x0:x1].astype(np.float32)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    rr = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / r
    bins = np.arange(0, rmax, step)
    idx = np.digitize(rr.ravel(), bins) - 1
    ok = (idx >= 0) & (idx < len(bins) - 1)
    v, idx = sub.ravel()[ok], idx[ok]
    n = len(bins) - 1
    s = np.bincount(idx, weights=v, minlength=n)
    c = np.bincount(idx, minlength=n)
    return bins[:-1] + step / 2, s / np.maximum(c, 1)


def estimate_psf_sigma(gray, cx, cy, r, step=0.01):
    """由盘缘 ESF 的 10-90% 宽度反推 PSF σ（像素）

    10-90% 宽度 ≈ 2.563σ（高斯边缘响应）。径向平均会把月海/环形山/一侧
    大气散射摊平，故只取盘缘 ±15% 环带内的跨越点，且要求"由内向外单调下降"。
    返回 (sigma, edge_px, quality)；quality 用于判断是否可信。
    """
    rs, prof = radial_esf(gray, cx, cy, r, step=step)
    inner = prof[rs < 0.6]
    outer = prof[(rs > 1.02) & (rs < 1.15)]
    if inner.size < 5 or outer.size < 5:
        return np.nan, np.nan, 0.0
    pl, bg = float(np.median(inner)), float(np.median(outer))
    if not np.isfinite(pl) or pl <= bg * 2:
        return np.nan, np.nan, 0.0
    lo = bg + 0.1 * (pl - bg)
    hi = bg + 0.9 * (pl - bg)
    sel = (rs > 0.85) & (rs < 1.12)
    rr, pp = rs[sel], prof[sel]

    def cross(level):
        for k in range(len(pp) - 1):
            if (pp[k] - level) * (pp[k + 1] - level) <= 0 and pp[k] > pp[k + 1]:
                t = (level - pp[k]) / (pp[k + 1] - pp[k] + 1e-12)
                return rr[k] + t * (rr[k + 1] - rr[k])
        return np.nan

    r10, r90 = cross(lo), cross(hi)
    w = r10 - r90
    if not np.isfinite(w) or w <= 0:
        return np.nan, np.nan, 0.0
    edge = w * r
    return float(edge / 2.563), float(edge), float(min(1.0, pl / max(bg, 1e-6) / 20.0))


def fuse_rois(rois, shifts, scale, base_xy, gains=None, biases=None,
              weights=None, out_size=None):
    """把各帧 ROI 融合到 scale 倍网格

    rois: [(roi_u16 or float)] 同尺寸 HxWx3
    shifts: [(dx,dy)] 或 2x3 仿射 M（ref 坐标 -> 帧坐标），M 由 register_roi 给
    base_xy: 参考帧 ROI 左上角在参考帧坐标中的位置（仅用于记录）
    gains/biases: 逐帧光度校正（先减 bias 再乘 gain，与主管线一致）
    weights: 逐帧融合权重
    out_size: (w,h)，默认 (W*scale, H*scale)
    返回 float32 0..1 的 HxWx3
    """
    n = len(rois)
    if n == 0:
        raise ValueError('没有可融合的帧')
    src0 = np.asarray(rois[0])
    h, w = src0.shape[:2]
    ow, oh = (int(round(w * scale)), int(round(h * scale))) if not out_size else out_size
    # 输出网格 -> 参考 ROI 坐标（像素中心对齐）
    xs = (np.arange(ow, dtype=np.float32) + 0.5) / scale - 0.5
    ys = (np.arange(oh, dtype=np.float32) + 0.5) / scale - 0.5
    X, Y = np.meshgrid(xs, ys)
    acc = np.zeros((oh, ow, 3), np.float64)
    wsum = 0.0
    for i in range(n):
        src = np.asarray(rois[i])
        img = src.astype(np.float32)
        if src.dtype == np.uint16:
            img = img / 65535.0
        elif img.max() > 1.5:
            img = img / 65535.0
        tr = shifts[i]
        arr = np.asarray(tr, dtype=np.float64)
        if arr.shape == (2, 3):
            mx = (arr[0, 0] * X + arr[0, 1] * Y + arr[0, 2]).astype(np.float32)
            my = (arr[1, 0] * X + arr[1, 1] * Y + arr[1, 2]).astype(np.float32)
        else:
            mx = (X + float(arr[0])).astype(np.float32)
            my = (Y + float(arr[1])).astype(np.float32)
        s = cv2.remap(img, mx, my, interpolation=cv2.INTER_CUBIC,
                      borderMode=cv2.BORDER_REFLECT)
        if s.ndim == 2:
            s = np.repeat(s[:, :, None], 3, axis=2)
        if biases is not None:
            s = s - float(biases[i])
        if gains is not None:
            s = s * float(gains[i])
        wgt = 1.0 if weights is None else float(weights[i])
        acc += s * wgt
        wsum += wgt
    if wsum <= 0:
        raise ValueError('融合权重之和为 0')
    return acc / wsum


def select_lucky(edges, min_keep=4, keep_ratio=1.25, weight_power=4.0,
                 w_min=0.2):
    """按盘缘 10-90% 宽度挑帧并定权（lucky imaging 思路）

    不同帧的 seeing/对焦差异会让 PSF 不同：糊帧不是"多一帧噪声平均"，
    而是把整堆往糊的方向拉。故只保留边缘宽度 ≤ 最锐帧 × keep_ratio 的帧
    （至少 min_keep 张，不够就取最锐的几张），权重 (e_min/e)^power。

    返回 (indices, weights)，weights 与 indices 同序、最大值为 1。
    """
    e = np.asarray(edges, dtype=np.float64)
    ok = np.isfinite(e) & (e > 0)
    e = np.where(ok, e, np.inf)
    order = np.argsort(e)
    e_min = float(e[order[0]])
    keep = [int(i) for i in order if e[i] <= e_min * keep_ratio]
    if len(keep) < min_keep:
        keep = [int(i) for i in order[:min(min_keep, len(order))]]
    keep = sorted(keep)
    w = np.clip((e_min / np.maximum(e[keep], 1e-9)) ** weight_power, w_min, 1.0)
    return keep, [float(v) for v in w]


def subject_tonemap(linear, mask_radius_px, center=None, gamma=2.2,
                    anchor_display=0.50, shoulder=0.80):
    """以主体为锚的显示映射

    黑主导画面的坑：全帧分位数定标会把白点落在黑天空上（P99.5 仍是天空），
    主体被拉顶成白饼。这里改为按**主体中位**曝光：
      - 增益把主体中位线性亮度映射到 anchor_display（伽马后），
        月面通常 0.5 —— 月海/高地/亮纹的层次都留在 0.3~0.8；
      - 黑点 = 0，天空保持黑，不抬背景（黑主导画面绝不能按全帧分位拉伸）；
      - 0.8 以上走软肩（tanh）压缩而非硬裁，亮纹不糊成一片白。
    linear: 0..1 HxWx3
    返回 float32 0..1 HxWx3（已 gamma）
    """
    a = np.clip(np.asarray(linear, np.float32), 0, 1)
    h, w = a.shape[:2]
    lum = a @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    if center is None:
        center = (w / 2.0, h / 2.0)
    cx, cy = center
    yy, xx = np.mgrid[0:h, 0:w]
    m = ((xx - cx) ** 2 + (yy - cy) ** 2) <= mask_radius_px ** 2
    disk = lum[m]
    if disk.size < 64:
        raise ValueError('主体掩膜太小')
    med = float(np.median(disk))
    if med <= 0:
        raise ValueError('主体亮度异常')
    k = (anchor_display ** gamma) / med          # 线性域增益
    out = np.power(np.clip(a * k, 0, 1), 1.0 / gamma)
    # 软肩：> shoulder 的部分平滑压到 1.0，避免亮纹硬裁成白块
    if 0 < shoulder < 1:
        over = out > shoulder
        if over.any():
            span = 1.0 - shoulder
            out[over] = shoulder + span * np.tanh((out[over] - shoulder) / span)
    return np.clip(out, 0, 1).astype(np.float32)


# ===== 行星摄影式多尺度锐化（à trous 小波）=====
# 为什么需要它：RL 只反卷积"光学 PSF"这一种模糊，而观感上的"锐"还有一大半
# 来自中频局部对比度（环形山壁、月海边界）。行星摄影（RegiStax/AutoStakkert）
# 一律用多尺度小波提中频——这是学习型超分最容易被误认为"分辨率更高"的地方。

def _atrous_kernel(level):
    """B3 样条（1,4,6,4,1)/16 的 à trous 核：抽头间隔 2^level"""
    n = 1 << int(level)
    k = np.zeros(4 * n + 1, np.float32)
    k[0::n] = np.array([1, 4, 6, 4, 1], np.float32) / 16.0
    return k.reshape(1, -1)


def atrous_blur(img, level):
    """à trous（带孔）B3 样条平滑；level 越大尺度越大"""
    k = _atrous_kernel(level)
    a = np.ascontiguousarray(img, np.float32)
    a = cv2.filter2D(a, -1, k, borderType=cv2.BORDER_REFLECT)
    a = cv2.filter2D(a, -1, k.reshape(-1, 1), borderType=cv2.BORDER_REFLECT)
    return a


def wavelet_sharpen(linear, gains=(0.6, 1.5, 1.3, 1.1), local_contrast=0.0,
                    local_sigma=24.0, mask=None, keep_median=True):
    """多尺度小波锐化（只作用于亮度，色度原样保留）

    linear: 0..1 HxWx3（线性或已 gamma 都行，建议在显示域用）
    gains: 各尺度细节层增益，从最细（1px）到大尺度；>1 提锐，<1 抑制
    local_contrast: 大尺度局部对比（"clarity"），0=关闭；0.1~0.2 是安全区
    mask: 参与锐化的区域（0..1 浮点，通常是羽化的月盘掩膜）。盘缘外不锐化，
          否则小波在亮/黑交界处必然产生黑边——行星软件也是手动擦掉边缘的。
    keep_median: 锐化后把亮度中位拉回原值（小波分解在有限图上不是严格零均值，
          不校正会让整体观感变暗/变亮）
    返回 float32 同尺寸，值域不保证在 0..1（由调用方 clip）
    """
    a = np.asarray(linear, np.float32)
    y = a @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    cur = y.copy()
    out = y.copy()
    if gains:
        for lv, g in enumerate(gains):
            sm = atrous_blur(cur, lv)
            detail = cur - sm
            out = out + (float(g) - 1.0) * detail
            cur = sm
    if local_contrast:
        lo = cv2.GaussianBlur(y, (0, 0), local_sigma)
        hp = y - lo
        # 软限幅，避免强边缘出现白/黑边
        out = out + local_contrast * np.tanh(hp / 0.05) * 0.05
    if keep_median:
        sel = (mask > 0.5).astype(bool) if mask is not None else (y > 1e-3)
        if sel.sum() > 64:
            m0 = float(np.median(y[sel]))
            m1 = float(np.median(out[sel]))
            if m1 > 1e-6 and m0 > 1e-6:
                out = out * (m0 / m1)
    # 按亮度比例回写到彩色（色度不锐化，避免色边）
    ratio = out / np.maximum(y, 1e-4)
    res = a * ratio[..., None]
    if mask is not None:
        m = np.clip(mask, 0, 1)[..., None]
        res = a * (1.0 - m) + res * m
    return res.astype(np.float32)
