# -*- coding: utf-8 -*-
"""月亮单帧 vs SR 客观测量（线性域，同一物理尺度）

- 月盘几何（外接圆半径）
- 边缘扩散函数（ESF）：径向平均亮度 vs r，10-90% 边缘宽度（换算到原生像素）
- 天空噪声 sigma（SR 先降采样到原生尺度再比，同尺度同区域）
- 细节对比：月盘内高频 RMS（带限到同一物理频率）
"""
import argparse
from pathlib import Path

import numpy as np
import cv2
import rawpy


def load_nef_linear(p, use_camera_wb=True):
    with rawpy.imread(str(p)) as raw:
        rgb = raw.postprocess(demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
                              half_size=False, use_camera_wb=use_camera_wb,
                              use_auto_wb=False, output_bps=16,
                              output_color=rawpy.ColorSpace.raw,
                              no_auto_bright=True, gamma=(1, 1))
    return rgb.astype(np.float32) / 65535.0


def load_dng(dng_path):
    import sys
    sys.path.insert(0, r'D:\Software files\photo_watermark')
    from sr_process import load_subifd_rgb
    a = load_subifd_rgb(dng_path)
    return a.astype(np.float32) / 65535.0


def find_disk(g):
    """返回 (cx, cy, r, bbox) —— 阈值取全局 3% 与峰值 15% 的较大者"""
    a = np.clip(g * 255.0, 0, 255).astype(np.uint8)
    thr = max(6, int(a.max() * 0.15))
    _, th = cv2.threshold(a, thr, 255, cv2.THRESH_BINARY)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    (x, y), r = cv2.minEnclosingCircle(c)
    bx, by, bw, bh = cv2.boundingRect(c)
    return float(x), float(y), float(r), (bx, by, bw, bh)


def radial_profile(g, cx, cy, r, step=0.02, rmax=1.25):
    """径向平均亮度（以 r 为单位），返回 (rs, mean, p16, p84)"""
    h, w = g.shape
    y0, y1 = max(0, int(cy - r * rmax)), min(h, int(cy + r * rmax) + 1)
    x0, x1 = max(0, int(cx - r * rmax)), min(w, int(cx + r * rmax) + 1)
    sub = g[y0:y1, x0:x1]
    yy, xx = np.mgrid[y0:y1, x0:x1]
    rr = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / r
    bins = np.arange(0, rmax, step)
    idx = np.digitize(rr.ravel(), bins) - 1
    ok = (idx >= 0) & (idx < len(bins) - 1)
    v = sub.ravel()[ok]
    idx = idx[ok]
    n = len(bins) - 1
    s = np.bincount(idx, weights=v, minlength=n)
    c = np.bincount(idx, minlength=n)
    mean = s / np.maximum(c, 1)
    # 分位数按 bin
    p16 = np.full(n, np.nan)
    p84 = np.full(n, np.nan)
    order = np.argsort(idx, kind='stable')
    idx_s, v_s = idx[order], v[order]
    starts = np.searchsorted(idx_s, np.arange(n))
    ends = np.searchsorted(idx_s, np.arange(n) + 1)
    for k in range(n):
        seg = v_s[starts[k]:ends[k]]
        if seg.size > 8:
            p16[k] = np.percentile(seg, 16)
            p84[k] = np.percentile(seg, 84)
    return bins[:-1] + step / 2, mean, p16, p84


def edge_width(rs, prof):
    """取盘内平台（r<0.6）中位数与背景（1.0~1.15）中位数，
    求 10%/90% 径向位置差（单位 r）。"""
    inner = prof[rs < 0.6]
    outer = prof[(rs > 1.02) & (rs < 1.15)]
    pl, bg = np.median(inner), np.nanmedian(outer)
    lo = bg + 0.1 * (pl - bg)
    hi = bg + 0.9 * (pl - bg)
    sel = (rs > 0.85) & (rs < 1.12)
    r = rs[sel]
    p = prof[sel]
    # 由外向内找跨越点（用线性插值）
    def cross(level):
        for k in range(len(p) - 1):
            if (p[k] - level) * (p[k + 1] - level) <= 0 and p[k] > p[k + 1]:
                t = (level - p[k]) / (p[k + 1] - p[k] + 1e-12)
                return r[k] + t * (r[k + 1] - r[k])
        return np.nan
    r90, r10 = cross(hi), cross(lo)
    return r10 - r90, (r10, r90, pl, bg)


def band_rms(g, mask, sigma_px):
    """掩膜内高通 RMS（带限到同一物理频率）"""
    lo = cv2.GaussianBlur(g.astype(np.float32), (0, 0), sigma_px)
    hp = g - lo
    return float(np.sqrt(np.mean(hp[mask] ** 2)))


def analyze(g, label, native_scale=1.0, out_prefix=None):
    d = find_disk(g)
    if d is None:
        print(f'{label}: 未找到月盘')
        return None
    cx, cy, r, bbox = d
    print(f'\n[{label}] shape={g.shape[1]}x{g.shape[0]}  月盘 中心=({cx:.1f},{cy:.1f}) '
          f'半径={r:.2f}  直径={2*r:.1f}  峰值={g.max():.4f}')
    rs, prof, p16, p84 = radial_profile(g, cx, cy, r)
    w, (r10, r90, pl, bg) = edge_width(rs, prof)
    w_native = w * r / native_scale
    print(f'  平台(盘内)= {pl:.4f}  背景= {bg:.5f}  对比度= {pl/max(bg,1e-9):.0f}x')
    print(f'  边缘 10-90% 宽度 = {w:.4f}r = {w*r:.2f}px(图内) = {w_native:.2f}px(原生等效)')
    sig = max(1.0, w * r / 2.563 / native_scale)  # 10-90% 宽 ≈ 2.563σ
    h, wd = g.shape
    yy, xx = np.mgrid[0:h, 0:wd]
    disk = ((xx - cx) ** 2 + (yy - cy) ** 2) <= (0.85 * r) ** 2
    # 天空噪声：环带外 1.3~2.0r
    sky = ((xx - cx) ** 2 + (yy - cy) ** 2 > (1.3 * r) ** 2) & \
          ((xx - cx) ** 2 + (yy - cy) ** 2 < (2.0 * r) ** 2)
    # 加权前先去掉离群（星点）
    sv = g[sky]
    med = np.median(sv)
    mad = np.median(np.abs(sv - med)) * 1.4826
    keep = np.abs(sv - med) < 6 * max(mad, 1e-6)
    noise = float(sv[keep].std()) if keep.sum() > 100 else float('nan')
    hr = band_rms(g, disk, 3.0 * native_scale / max(r, 1) * r if False else 3.0)
    print(f'  天空噪声 sigma= {noise*65535:.3f}DN ({noise:.3e})  '
          f'盘内高频RMS(σ=3px)= {hr*65535:.2f}DN')
    # 边缘剖面采样（便于画图/核对）
    for rr in (0.90, 0.95, 0.97, 0.99, 1.00, 1.01, 1.03, 1.05, 1.10):
        k = int(np.argmin(np.abs(rs - rr)))
        print(f'    r={rs[k]:.2f}: {prof[k]:.5f}  [{p16[k]:.5f},{p84[k]:.5f}]')
    return dict(cx=cx, cy=cy, r=r, bbox=bbox, edge_w=w, edge_native=w_native,
                sigma_est=sig, noise=noise, hf=hr, prof=(rs, prof))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--nef', default=r'C:\Users\LQL\Pictures\raw\月亮\DSC_9064.NEF')
    ap.add_argument('--dng', default=r'C:\Users\LQL\Pictures\raw\月亮\SR输出\月亮_9帧_SR_2x.dng')
    ap.add_argument('--scale', type=float, default=2.0)
    a = ap.parse_args()

    single = load_nef_linear(a.nef)
    # 只留月盘周边 ROI，省内存
    d = find_disk(single.mean(axis=2))
    cx, cy, r, _ = d
    pad = int(r * 2.2)
    x0, y0 = max(0, int(cx - pad)), max(0, int(cy - pad))
    x1, y1 = min(single.shape[1], int(cx + pad)), min(single.shape[0], int(cy + pad))
    sc = single[y0:y1, x0:x1].copy()
    sc_c = (cx - x0, cy - y0)
    m1 = analyze(sc.mean(axis=2), '单帧 DSC_9064', native_scale=1.0)
    del single

    sr = load_dng(a.dng)
    print(f'\nSR DNG: {sr.shape[1]}x{sr.shape[0]}  ({a.dng})')
    srg = sr.mean(axis=2)
    m2 = analyze(srg, f'SR x{a.scale:g}', native_scale=a.scale)
    if m1 and m2:
        print('\n=== 对比 ===')
        print(f'  边缘 10-90% 宽度：单帧 {m1["edge_native"]:.2f}px(原生) → '
              f'SR {m2["edge_native"]:.2f}px(原生等效) = {m1["edge_native"]/max(m2["edge_native"],1e-9):.2f}x 更窄')
        print(f'  天空噪声：单帧 {m1["noise"]*65535:.2f}DN → '
              f'SR {m2["noise"]*65535:.2f}DN(2x 尺度原生像素面积)')
        print(f'  盘内高频 RMS：单帧 {m1["hf"]*65535:.2f}DN → SR {m2["hf"]*65535:.2f}DN')


if __name__ == '__main__':
    main()
