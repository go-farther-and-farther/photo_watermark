#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""超分输出分辨率客观指标：斜边 MTF50（ISO 12233 风格）+ 噪声底 + 100% 对比裁剪

对比对象（A/B 像素对齐于 SR 输出网格）：
  A 多帧 SR    —— Linear DNG SubIFD（无压缩 LinearRaw，线性相机域）
  B 插值基线  —— 参考帧同域解码 + Lanczos 2x（回答"比暴力放大多多少真细节"）
  C 原生参照  —— 参考帧原生 1x（原生解像力，独立换算报告）
用法：
  python devtests/sr_resolution.py                       # 默认 Z6 8帧测试集
  python devtests/sr_resolution.py <dng> <nef> [x y w h] # 自定义（含重叠区裁剪）

指标口径：
  MTF50(cyc/px)——MTF 跌至 0.5 的空间频率，越高=真实细节越多；
  LW/PH = MTF50 × 图高像素，跨尺度可比（A/B 用 2x 网格图高，C 用 1x）；
  噪声底 = 平坦区高通残差的稳健 σ（1.4826×MAD）。
"""
import struct
import sys
from pathlib import Path

import numpy as np
import cv2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_DNG = r'C:\Users\LQL\Pictures\raw\100NCZ_6\SR输出\dng测试3_adobe结构_2x.dng'
DEFAULT_NEF = r'C:\Users\LQL\Pictures\raw\100NCZ_6\DSC_0015.NEF'
DEFAULT_CROP = (15, 19, 6044, 3996)   # 重叠区在参考帧内的 (x, y, w, h)，即 SR 网格锚点
OUT_DIR = PROJECT_ROOT / 'devtests' / 'res_report'
TILE = 512          # 分析窗口（2x 网格）
N_TILES = 14        # 参与统计的边窗口数
MIN_ANGLE, MAX_ANGLE = 0.3, 25.0   # 边与轴夹角（城市竖直边实测 ~0.6°；0.3° 已够 0.1px 分箱相位多样性）
BIN = 0.1           # ESF 距离分箱 (px)


def load_dng_subifd_g(path):
    """读 Linear DNG 的 SubIFD 瓦片，返回 G 通道 u16（tifffile 不暴露 SubIFD 需手动解析）"""
    import tifffile
    tf = tifffile.TiffFile(str(path))
    fh = tf.filehandle
    off = tf.pages[0].tags[0x014A].value[0]
    fh.seek(off)
    n = struct.unpack('<H', fh.read(2))[0]
    tags = {}
    base = off + 2
    for k in range(n):
        fh.seek(base + k * 12)
        code, typ, cnt = struct.unpack('<HHI', fh.read(8))
        tags[code] = (typ, cnt, fh.read(4))

    def rd(code, nt=None):
        typ, cnt, vo = tags[code]
        n2 = cnt if nt is None else nt
        esz, fmt = {3: (2, 'H'), 4: (4, 'I')}[typ]
        if esz * n2 <= 4:
            return struct.unpack('<' + fmt * n2, vo[:esz * n2])
        fh.seek(struct.unpack('<I', vo)[0])
        return struct.unpack('<' + fmt * n2, fh.read(esz * n2))

    w, h = rd(0x0100)[0], rd(0x0101)[0]
    tw_, tl = rd(0x0142)[0], rd(0x0143)[0]
    assert rd(0x0106)[0] == 34892, 'SubIFD 不是 LinearRaw'
    toff, tcnt = rd(0x0144), rd(0x0145)
    ny, nx = (h + tl - 1) // tl, (w + tw_ - 1) // tw_
    rgb = np.empty((h, w, 3), np.uint16)
    for i in range(len(toff)):
        ty, tx = divmod(i, nx)
        th, tw = min(tl, h - ty * tl), min(tw_, w - tx * tw_)
        fh.seek(toff[i])
        tile = np.frombuffer(fh.read(tcnt[i]), '<u2', tl * tw_ * 3).reshape(tl, tw_, 3)
        rgb[ty*tl:ty*tl+th, tx*tw_:tx*tw_+tw] = tile[:th, :tw]
    print(f"  DNG SubIFD: {w}x{h}, {len(toff)} 瓦片")
    return rgb[:, :, 1].copy()


def load_ref_frame_g(nef, crop):
    """参考帧同域解码（nocam 线性域，与 DNG 数据同口径），返回 (crop_G_u16, lanczos2x_G_u16)"""
    import sr_process
    x, y, w, h = crop
    print(f"  解码参考帧（ nocam 线性域）...", flush=True)
    img = sr_process.read_raw_camera_linear(nef, half_size=False, use_camera_wb=False)
    crop_rgb = img[y:y+h, x:x+w]
    del img
    g1x = np.clip(crop_rgb[:, :, 1] * 65535.0 + 0.5, 0, 65535).astype(np.uint16)
    big = cv2.resize(crop_rgb, (w * 2, h * 2), interpolation=cv2.INTER_LANCZOS4)
    g2x = np.clip(big[:, :, 1] * 65535.0 + 0.5, 0, 65535).astype(np.uint16)
    print(f"  1x 裁剪 {w}x{h} -> Lanczos 2x {w*2}x{h*2}")
    return g1x, g2x


def pick_tiles(g, n_want, exclude_border=64):
    """按『边缘能量×方向一致性』挑分析窗口，贪心散开避免扎堆"""
    g32 = g.astype(np.float32)
    gx = cv2.Sobel(g32, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g32, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    h, w = g.shape
    cands = []
    ys = range(exclude_border, h - TILE - exclude_border, 256)
    for ty in ys:
        for tx in range(exclude_border, w - TILE - exclude_border, 256):
            m = mag[ty:ty+TILE, tx:tx+TILE]
            strong = m > np.percentile(m, 97)
            if strong.sum() < TILE * TILE * 0.005:
                continue
            # 结构张量一致性：边方向越单一，越适合拟合一条直线
            jxx = (gx[ty:ty+TILE, tx:tx+TILE] ** 2)[strong].sum()
            jyy = (gy[ty:ty+TILE, tx:tx+TILE] ** 2)[strong].sum()
            jxy = (gx[ty:ty+TILE, tx:tx+TILE] * gy[ty:ty+TILE, tx:tx+TILE])[strong].sum()
            tr = jxx + jyy + 1e-9
            coherence = np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2) / tr  # 0..1
            score = float(m[strong].mean() * coherence)
            if score > 0:
                cands.append((score, tx, ty))
    cands.sort(reverse=True)
    picked = []
    for score, tx, ty in cands:
        if all((tx - px) ** 2 + (ty - py) ** 2 > (2.2 * TILE) ** 2 for px, py, _ in picked):
            picked.append((tx, ty, score))
        if len(picked) >= n_want:
            break
    return picked


def _subpix_peaks(p, thr):
    """一维剖面里的局部峰（亚像素抛物线内插），返回 [(idx, height, subpos)]"""
    idx = np.flatnonzero((p[1:-1] > thr) & (p[1:-1] >= p[:-2]) & (p[1:-1] >= p[2:])) + 1
    out = []
    for i in idx:
        y0, y1, y2 = p[i-1], p[i], p[i+1]
        denom = y0 - 2 * y1 + y2
        delta = 0.5 * (y0 - y2) / denom if denom != 0 else 0.0
        out.append((int(i), float(y1), float(i) + float(np.clip(delta, -1, 1))))
    return out


def fit_edge_line(tile, return_debug=False):
    """窗口内 RANSAC 拟合主导斜边的亚像素直线，返回 (axis, a, b) 或 None

    axis='x' 表示 x = a*y + b（近竖边，采样 |gx| 峰）；axis='y' 同理近横边。
    城市夜景窗口内有多条平行边且边会被亮窗/灯牌打断：整行质心会在边间跳动、
    顺序链迹一断就断（实测链长 <120/512）——改为把全部亚像素峰撒进
    (行号, 位置) 点云做 RANSAC，最强共识集即主导边，天然免疫多边与间断；
    内点集再加权精拟合两轮。要求边与轴夹角 [1°, 25°]、内点覆盖与直线度达标。
    """
    t32 = tile.astype(np.float32)
    gx = cv2.Sobel(t32, cv2.CV_32F, 1, 0, ksize=5)
    gy = cv2.Sobel(t32, cv2.CV_32F, 0, 1, ksize=5)
    rng = np.random.default_rng(0)
    for axis in ('x', 'y'):
        prof = np.abs(gx) if axis == 'x' else np.abs(gy)
        n_scan = prof.shape[0]
        thr = max(np.percentile(prof, 99) * 0.3, 1e-6)
        pi, px, pw = [], [], []
        for i in range(n_scan):
            for _, hgt, sub in _subpix_peaks(prof[i] if axis == 'x' else prof[:, i], thr):
                pi.append(i)
                px.append(sub)
                pw.append(hgt)
        if len(pi) < 150:
            if return_debug:
                print(f"    [dbg] axis={axis} 峰数不足 {len(pi)}")
            continue
        ai = np.array(pi, np.float64)
        ax = np.array(px, np.float64)
        best_inl, best_cnt = None, 0
        for _ in range(400):
            k = rng.choice(len(ai), 2, replace=False)
            if abs(ai[k[0]] - ai[k[1]]) < n_scan * 0.3:
                continue          # 两采样点必须离得远，否则线方向随机
            a = (ax[k[1]] - ax[k[0]]) / (ai[k[1]] - ai[k[0]])
            b = ax[k[0]] - a * ai[k[0]]
            inl = np.abs(ax - (a * ai + b)) < 1.2
            if inl.sum() > best_cnt:
                best_cnt, best_inl = int(inl.sum()), inl
        if best_inl is None or best_cnt < n_scan * 0.35:
            if return_debug:
                print(f"    [dbg] axis={axis} RANSAC 共识不足 {best_cnt}")
            continue
        for _ in range(2):        # 内点加权精拟合 + 重选内点
            w = pw_arr = np.array(pw, np.float64)[best_inl]
            Amat = np.vstack([ai[best_inl], np.ones(best_inl.sum())]).T
            coef, *_ = np.linalg.lstsq(Amat * w[:, None], ax[best_inl] * w, rcond=None)
            a, b = float(coef[0]), float(coef[1])
            best_inl = np.abs(ax - (a * ai + b)) < 1.0
        a = float(a)
        resid = ax[best_inl] - (a * ai[best_inl] + b)
        wgt = np.array(pw, np.float64)[best_inl]
        sigma = float(np.sqrt((wgt * resid ** 2).sum() / wgt.sum()))
        span = float(ai[best_inl].max() - ai[best_inl].min())
        angle = abs(np.degrees(np.arctan(a)))
        if return_debug:
            print(f"    [dbg] axis={axis} 内点={best_cnt} 跨度={span:.0f} "
                  f"angle={angle:.1f}° sigma={sigma:.2f}px")
        if (MIN_ANGLE <= angle <= MAX_ANGLE and sigma < 0.6
                and span >= n_scan * 0.5 and best_inl.sum() >= n_scan * 0.35):
            return (axis, a, b)
    return None


def _small(x, f):
    h, w = x.shape
    return cv2.resize(x.astype(np.float32), (w // f, h // f), interpolation=cv2.INTER_AREA)


def local_shift(a_t, x_t, fa, fx):
    """窗口级相位相关：x_t 内容相对 a_t 的局部平移，单位=x_t 自身像素（右/下为正）

    a_t 按 fa、x_t 按 fx 降采样到同尺寸后相关；分辨率=f 是显式参数，
    因为 a/b 是 2x 网格而 c 是 1x 网格，同物理覆盖对应不同降采样系数。
    """
    h, w = a_t.shape
    pa = cv2.resize(a_t.astype(np.float32), (w // fa, h // fa), interpolation=cv2.INTER_AREA)
    px = cv2.resize(x_t.astype(np.float32), (w // fa, h // fa), interpolation=cv2.INTER_AREA)
    win = cv2.createHanningWindow((w // fa, h // fa), cv2.CV_32F)
    (dx, dy), resp = cv2.phaseCorrelate(pa * win, px * win)
    return dx * fx, dy * fx, float(resp)


def refine_line(tile, seed_line):
    """在锚线 ±3px 内做梯度加权质心精拟合（同一条物理边的局部定位），失败返回 None

    窗口内平行边多时，各图独立 RANSAC 会各锁一条边——逐窗口局部对齐后，
    以 A（最高信噪比）的线为锚，B/C 只在锚线附近精确定位。
    """
    line = seed_line
    for win in (6.0, 3.0):     # 两阶段：宽窗吸收局部对齐残差，窄窗收敛
        axis, a, b = line
        t32 = tile.astype(np.float32)
        gx = cv2.Sobel(t32, cv2.CV_32F, 1, 0, ksize=5)
        gy = cv2.Sobel(t32, cv2.CV_32F, 0, 1, ksize=5)
        prof = np.abs(gx) if axis == 'x' else np.abs(gy)
        n_scan = prof.shape[0]
        thr = max(np.percentile(prof, 99) * 0.1, 1e-6)
        pos, cen, wgt = [], [], []
        for i in range(n_scan):
            pr = prof[i] if axis == 'x' else prof[:, i]
            idx = np.arange(pr.size)
            pred = a * i + b
            m = (pr > thr) & (idx >= pred - win) & (idx <= pred + win)
            if m.sum() < 2:
                continue
            wl = pr[m]
            pos.append(i)
            cen.append(float((idx[m] * wl).sum() / wl.sum()))
            wgt.append(float(wl.sum()))
        if len(pos) < n_scan * 0.35:
            return None
        pos = np.array(pos, np.float64)
        cen = np.array(cen, np.float64)
        wgt = np.array(wgt, np.float64)
        coef, *_ = np.linalg.lstsq(np.vstack([pos, np.ones_like(pos)]).T * wgt[:, None],
                                   cen * wgt, rcond=None)
        line = (axis, float(coef[0]), float(coef[1]))
    # 角度门槛只属 RANSAC 发现阶段（fit_edge_line）；锚定精拟合信任 A 的锚线，
    # 不对 B/C 自己的斜率重设卡（单帧 256 行的斜率估计噪声会误杀）
    return line


def esf_mtf(tile, line):
    """按拟合直线法向采集亚像素 ESF -> LSF -> MTF，返回 (mtf50, mtf, freqs)"""
    axis, a, b = line
    t = tile.astype(np.float64)
    h, w = t.shape
    if axis == 'x':
        yy, xx = np.mgrid[0:h, 0:w]
        d = (xx - (a * yy + b)) / np.sqrt(1 + a * a)      # 像素到直线的有向距离
    else:
        yy, xx = np.mgrid[0:h, 0:w]
        d = (yy - (a * xx + b)) / np.sqrt(1 + a * a)
    m = np.abs(d) < 6.0
    d, v = d[m], t[m]
    edges = np.arange(-6.0, 6.0 + BIN, BIN)
    idx = np.digitize(d, edges) - 1
    ok = (idx >= 0) & (idx < len(edges) - 1)
    esf = np.full(len(edges) - 1, np.nan)
    for k in range(len(edges) - 1):
        sel = v[ok & (idx == k)]
        if sel.size >= 5:
            esf[k] = sel.mean()
    good = np.isfinite(esf)
    if good.sum() < len(esf) * 0.6:
        return None
    # 缺口线性插值 + 轻度平滑（不影响 MTF50 量级）
    esf = np.interp(np.arange(len(esf)), np.flatnonzero(good), esf[good])
    esf = cv2.blur(esf.reshape(1, -1).astype(np.float32), (1, 3)).ravel()
    if esf[-1] < esf[0]:                    # 统一暗->亮极性
        esf = esf[::-1]
    lsf = np.gradient(esf)
    n = len(lsf)
    mtf = np.abs(np.fft.rfft(lsf * np.hanning(n)))
    if mtf[0] <= 0:
        return None
    mtf = mtf / mtf[0]
    freqs = np.arange(mtf.size) / (n * BIN)   # cyc/px
    valid = freqs <= 0.5
    freqs, mtf = freqs[valid], mtf[valid]
    below = np.flatnonzero(mtf < 0.5)
    if below.size == 0 or below[0] == 0:
        return None
    k = below[0]
    f0, f1, m0, m1 = freqs[k-1], freqs[k], mtf[k-1], mtf[k]
    mtf50 = f0 + (0.5 - m0) * (f1 - f0) / (m1 - m0)
    return float(mtf50), mtf, freqs


def noise_sigma(tile):
    """平坦区高通残差稳健 σ（1.4826×MAD），抗场景结构污染"""
    t = tile.astype(np.float32)
    hp = t - cv2.blur(t, (5, 5))
    return float(1.4826 * np.median(np.abs(hp - np.median(hp))))


def save_crops(pairs, out_dir):
    """保存 100% 对比裁剪：每窗口 A/B 上下拼 + C 原生近邻放大参照"""
    out_dir.mkdir(parents=True, exist_ok=True)
    half = TILE // 2
    paths = []
    for k, (tx, ty, a_tile, b_tile, c_tile) in enumerate(pairs):
        a8 = cv2.normalize(a_tile, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        b8 = cv2.normalize(b_tile, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        c8 = cv2.normalize(c_tile, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        c_up = cv2.resize(c8, (TILE, TILE), interpolation=cv2.INTER_NEAREST)
        gap = np.full((TILE, 4), 128, np.uint8)
        row = np.hstack([a8, gap, b8, gap, c_up])
        bar = np.full((28, row.shape[1]), 30, np.uint8)
        cv2.putText(bar, f'tile{k}: SR 8f | single+Lanczos2x | native1x(nearest)',
                    (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.5, 255, 1, cv2.LINE_AA)
        img = np.vstack([bar, row])
        p = out_dir / f'res_tile{k}_x{tx}_y{ty}.png'
        ok, buf = cv2.imencode('.png', img)
        if ok:
            buf.tofile(str(p))     # 兼容非 ASCII 路径的写法
            paths.append(p)
    return paths


def radial_psd(g, nband=50, fmax=0.5):
    """窗口径向功率谱密度（Hann 窗，各向同性平均），返回 (各频段 PSD, 频段边界)"""
    x = g.astype(np.float32)
    x = x - x.mean()
    w = np.hanning(x.shape[0])[:, None] * np.hanning(x.shape[1])[None, :]
    F = np.fft.rfft2(x * w)
    p = (F.real ** 2 + F.imag ** 2) / x.size   # Parseval 归一：不同窗口尺寸方差量纲一致
    fy = np.fft.fftfreq(x.shape[0])[:, None]
    fx = np.fft.rfftfreq(x.shape[1])[None, :]
    r = np.sqrt(fy ** 2 + fx ** 2)
    bands = np.linspace(0, fmax, nband + 1)
    out = np.full(nband, np.nan)
    for i in range(nband):
        m = (r >= bands[i]) & (r < bands[i + 1])
        if m.any():
            out[i] = p[m].mean()
    return out, bands


def pick_flat_windows(g, n_want, exclude_border=64):
    """挑最平坦（梯度能量最低）的窗口，用于估噪声底/PSD 地板"""
    g32 = g.astype(np.float32)
    gx = cv2.Sobel(g32, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g32, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    h, w = g.shape
    cands = []
    for ty in range(exclude_border, h - TILE - exclude_border, 256):
        for tx in range(exclude_border, w - TILE - exclude_border, 256):
            cands.append((float(mag[ty:ty+TILE, tx:tx+TILE].mean()), tx, ty))
    cands.sort()
    picked = []
    for e, tx, ty in cands:
        if all((tx - px) ** 2 + (ty - py) ** 2 > (2.2 * TILE) ** 2 for px, py in picked):
            picked.append((tx, ty))
        if len(picked) >= n_want:
            break
    return picked


def main():
    argv = [a for a in sys.argv[1:]]
    dng = Path(argv[0]) if argv else Path(DEFAULT_DNG)
    nef = Path(argv[1]) if len(argv) > 1 else Path(DEFAULT_NEF)
    crop = (tuple(int(v) for v in argv[2:6]) if len(argv) >= 6 else DEFAULT_CROP)

    print(f"== 分辨率指标：{dng.name} vs {nef.name} ==")
    print("[1/4] 读数据")
    a_g = load_dng_subifd_g(dng)
    c_g, b_g = load_ref_frame_g(nef, crop)
    assert a_g.shape == b_g.shape

    print("[2/4] 挑窗口（内容窗 + 平坦窗）")
    content = pick_tiles(a_g, 24)
    flat = pick_flat_windows(a_g, 12)
    print(f"  内容窗 {len(content)} 个, 平坦窗 {len(flat)} 个")

    def window_psds():
        acc = {k: {'c': [], 'f': []} for k, g in (('A', 0), ('B', 1), ('C', 2))}
        for tx, ty, _ in content:
            acc['A']['c'].append(radial_psd(a_g[ty:ty+TILE, tx:tx+TILE])[0])
            acc['B']['c'].append(radial_psd(b_g[ty:ty+TILE, tx:tx+TILE])[0])
            acc['C']['c'].append(radial_psd(c_g[ty//2:(ty+TILE)//2, tx//2:(tx+TILE)//2])[0])
        for tx, ty in flat:
            acc['A']['f'].append(radial_psd(a_g[ty:ty+TILE, tx:tx+TILE])[0])
            acc['B']['f'].append(radial_psd(b_g[ty:ty+TILE, tx:tx+TILE])[0])
            acc['C']['f'].append(radial_psd(c_g[ty//2:(ty+TILE)//2, tx//2:(tx+TILE)//2])[0])
        out = {}
        for k in acc:
            out[k] = (np.nanmedian(np.array(acc[k]['c']), axis=0),
                      np.nanmedian(np.array(acc[k]['f']), axis=0))
        return out, radial_psd(a_g[:TILE, :TILE])[1]

    print("[3/4] 径向 PSD 分频段统计")
    psd, bands = window_psds()

    def band_energy(psd_curve, lo, hi, scale=1.0):
        """[lo,hi]（按 2x 网格 cyc/px 计，scale 换算 C 的 1x 频率）频段 PSD 均值"""
        m = (bands[:-1] * scale >= lo) & (bands[:-1] * scale < hi)
        return float(np.nanmean(psd_curve[m]))

    print("[4/4] 结果")
    hdr = f"{'频段(cyc/px@2x)':>18s} {'A=SR':>14s} {'B=Lanczos2x':>14s} {'A/B':>7s} {'C=原生1x':>14s}"
    print(hdr)
    for lo, hi, name in [(0.05, 0.15, '低频 0.05-0.15'),
                         (0.15, 0.30, '中频 0.15-0.30'),
                         (0.30, 0.45, '高频 0.30-0.45'),
                         (0.45, 0.50, '顶频 0.45-0.50(近Nyquist)')]:
        ea_c, ea_f = psd['A']
        eb_c, eb_f = psd['B']
        ec_c, ec_f = psd['C']
        va = band_energy(ea_c, lo, hi) - band_energy(ea_f, lo, hi)   # 扣平坦窗地板
        vb = band_energy(eb_c, lo, hi) - band_energy(eb_f, lo, hi)
        vc = band_energy(ec_c, lo, hi, scale=0.5) - band_energy(ec_f, lo, hi, scale=0.5)
        ratio = f"{va/vb:7.2f}" if vb > 0 else "    n/a"
        print(f"{name:>18s} {va:>14.3e} {vb:>14.3e} {ratio} {vc:>14.3e}")

    na = np.median([noise_sigma(a_g[ty:ty+TILE, tx:tx+TILE]) for tx, ty in flat])
    nb = np.median([noise_sigma(b_g[ty:ty+TILE, tx:tx+TILE]) for tx, ty in flat])
    nc = np.median([noise_sigma(c_g[ty//2:(ty+TILE)//2, tx//2:(tx+TILE)//2])
                    for tx, ty in flat])
    print(f"\n平坦窗噪声底  SR: {na:.1f} | 单帧: {nb:.1f} | 降幅 {nb/na:.2f}x (8帧理论 ~2.83x)")
    print("说明：高频段 A/B 比值 >1 即 SR 携带插值给不出的真实细节；")
    print("      已扣除各图平坦窗 PSD 地板（噪声），C 频率轴已换算到 2x 等效。")

    vis = []
    for tx, ty, _ in content[:3]:
        vis.append((tx, ty, a_g[ty:ty+TILE, tx:tx+TILE],
                    b_g[ty:ty+TILE, tx:tx+TILE],
                    c_g[ty//2:(ty+TILE)//2, tx//2:(tx+TILE)//2]))
    paths = save_crops(vis, OUT_DIR)
    print(f"\n100% 对比裁剪已存: {[p.name for p in paths]}")




if __name__ == '__main__':
    main()
