# -*- coding: utf-8 -*-
"""A/B 两版超分 DNG 的同区域径向 PSD 分频段对比

两版画布因透视余量内收时有偏移：区域按"基准图坐标"给定，
新图坐标 = 基准坐标 - (shift_x, shift_y)。

用法:
  python devtests/sr_ab_psd.py <新.dng> <基准.dng> <shift_x> <shift_y> <x> <y> <w> <h>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'devtests'))

import numpy as np

from sr_resolution import (load_dng_subifd_g, radial_psd, pick_tiles,
                           pick_flat_windows, TILE)

BANDS = [(0.05, 0.15, '低频 0.05-0.15'),
         (0.15, 0.30, '中频 0.15-0.30'),
         (0.30, 0.45, '高频 0.30-0.45'),
         (0.45, 0.50, '顶频 0.45-0.50(近Nyquist)')]


def band_energy(psd_curve, bands, lo, hi):
    m = (bands[:-1] >= lo) & (bands[:-1] < hi)
    return float(np.nanmean(psd_curve[m]))


def band_stats(g, label, windows=None):
    """windows=None 在本图选窗；传 (content, flat) 则两图共用同一组窗（公平对比）"""
    if windows is None:
        windows = (pick_tiles(g, 24), pick_flat_windows(g, 12))
    content, flat = windows
    cc = [radial_psd(g[ty:ty + TILE, tx:tx + TILE])[0] for tx, ty, _ in content]
    ff = [radial_psd(g[ty:ty + TILE, tx:tx + TILE])[0] for tx, ty in flat]
    bands = radial_psd(g[:TILE, :TILE])[1]
    out = {}
    ec = np.nanmedian(np.array(cc), axis=0)
    ef = np.nanmedian(np.array(ff), axis=0)
    for lo, hi, name in BANDS:
        out[name] = (band_energy(ec, bands, lo, hi) - band_energy(ef, bands, lo, hi),
                     band_energy(ef, bands, lo, hi))
    print(f'{label}: 内容窗 {len(content)}, 平坦窗 {len(flat)}')
    return out


def main():
    dng_new, dng_base = Path(sys.argv[1]), Path(sys.argv[2])
    sx, sy, x, y, w, h = (int(v) for v in sys.argv[3:9])
    a = load_dng_subifd_g(dng_new)[y - sy:y - sy + h, x - sx:x - sx + w]
    b = load_dng_subifd_g(dng_base)[y:y + h, x:x + w]
    assert a.shape == b.shape, (a.shape, b.shape)
    print(f'== A/B PSD 对比：{dng_new.name} vs {dng_base.name} ==')
    print(f'   区域(基准坐标) ({x},{y}) {w}x{h}，新图偏移 (-{sx},-{sy})')
    windows = (pick_tiles(b, 24), pick_flat_windows(b, 12))   # 基准图选窗，两图共用
    sa = band_stats(a, 'A=' + dng_new.name, windows)
    sb = band_stats(b, 'B=' + dng_base.name, windows)
    print('-' * 78)
    print(f"{'频段(cyc/px@2x)':>22s} {'A内容':>12s} {'B内容':>12s} {'A/B':>7s} {'A噪底/B噪底':>12s}")
    for lo, hi, name in BANDS:
        ea, na = sa[name]
        eb, nb = sb[name]
        r = f'{ea / eb:7.3f}' if eb > 0 else '    n/a'
        rn = f'{na / nb:11.3f}' if nb > 0 else '        n/a'
        print(f'{name:>22s} {ea:12.4g} {eb:12.4g} {r} {rn}')
    print('(内容能量 A/B>1 = 新版细节更多；噪底比<1 = 新版更干净)')


if __name__ == '__main__':
    main()
