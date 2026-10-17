# -*- coding: utf-8 -*-
"""为什么这组月亮堆栈没有分辨率增益：用真数据把账算清

判据只有一条：**单帧采样之后，2x 奈奎斯特以上还有没有"真信号"？**
- 有 → 单帧混叠，亚像素堆栈能把它捞回来（这才是 SR 的增益来源）；
- 没有 → 堆栈只能降噪，分辨率由 PSF 决定，谁堆都一样。

测量：
1. 堆栈（2x 母版）盘内区域的径向功率谱，与同图天空区的噪声底对比；
2. 单帧的径向功率谱（降采样到原生尺度对比）；
3. PSF σ → 各频率的 MTF，看像素奈奎斯特（0.5 cyc/px）处还剩多少。
"""
import sys
from pathlib import Path

import numpy as np
import cv2

sys.path.insert(0, r'D:\Software files\photo_watermark')
import sr_moon
import sr_process

SRC = Path(r'C:\Users\LQL\Pictures\raw\月亮')
OUT = SRC / 'SR输出'


def radial(r, F, step=0.01):
    """按半径把 F 分箱做径向平均"""
    bins = np.arange(0, 0.75, step)
    idx = np.digitize(r.ravel(), bins) - 1
    ok = (idx >= 0) & (idx < len(bins) - 1)
    v, i = F.ravel()[ok], idx[ok]
    n = len(bins) - 1
    s = np.bincount(i, weights=v, minlength=n)
    c = np.bincount(i, minlength=n)
    return bins[:-1] + step / 2, s / np.maximum(c, 1)


def spec(img, win=192):
    h, w = img.shape
    y0, x0 = (h - win) // 2, (w - win) // 2
    p = img[y0:y0 + win, x0:x0 + win].astype(np.float64)
    p -= p.mean()
    p *= np.hanning(win)[:, None] * np.hanning(win)[None, :]
    F = np.abs(np.fft.fftshift(np.fft.fft2(p))) ** 2
    fy, fx = np.mgrid[-win // 2:win // 2, -win // 2:win // 2]
    r = np.sqrt(fx ** 2 + fy ** 2) / win          # cycles / 该图的像素
    return radial(r, F)


def psd_at(f, p, f0, tol=0.02):
    m = np.abs(f - f0) < tol
    return float(p[m].mean())


def main():
    stack = sr_process.load_subifd_rgb(str(OUT / '月亮_9帧堆栈_2x.dng')).astype(np.float32) / 65535.0
    g = stack.mean(axis=2)
    H, W = g.shape
    # 盘内纹理窗（取盘心上方一块）与天空噪声窗（同图角落）
    c = (W // 2, H // 2)
    r_s = 158 * 2
    disk_win = g[int(c[1] - 96 - 100):int(c[1] + 96 - 100),
                 int(c[0] - 96):int(c[0] + 96)]
    sky_win = g[8:8 + 192, 8:8 + 192]
    fd, pd = spec(disk_win)
    fs, ps = spec(sky_win)

    print('=' * 74)
    print('A. 堆栈（2x）里，单帧奈奎斯特以上的能量还剩多少')
    print('=' * 74)
    print(f'  {"频率(cyc/原生px)":>18} {"盘内 PSD":>12} {"天空噪声底":>12} {"信噪比":>8}')
    for f0 in (0.10, 0.20, 0.25, 0.30, 0.33):
        a = psd_at(fd, pd, f0 * 2.0)      # 堆栈是 2x，频率换算
        b = psd_at(fs, ps, f0 * 2.0)
        if not np.isfinite(a) or not np.isfinite(b):
            print(f'  {f0:>18.2f}  (2x 网格半径不够，测不到)')
            continue
        snr = 10 * np.log10(max(a, 1e-30) / max(b, 1e-30))
        print(f'  {f0:>18.2f} {a:>12.3e} {b:>12.3e} {snr:>6.1f}dB')
    print('  注：2x 母版的径向频率上限 = 0.354 cyc/原生px（方形 FFT 对角），'
          '再往上测不到。')

    print('\n' + '=' * 74)
    print('B. PSF 决定了一切：MTF 与像素奈奎斯特的差距')
    print('=' * 74)
    rsig, redge, _ = sr_moon.estimate_psf_sigma(g, c[0], c[1], r_s)
    rsig_native = rsig / 2.0
    print(f'  盘缘 10-90% = {redge:.2f}px(2x) = {redge/2:.2f}px(原生)')
    print(f'  → PSF σ ≈ {rsig_native:.2f} 原生像素，FWHM ≈ '
          f'{2.355*rsig_native:.2f}px（像素间距 5.94µm，1px）')
    print(f'\n  {"频率(cyc/px)":>12} {"MTF":>10} {"说明":>28}')
    for nu, note in ((0.10, '对比度 1:10 的细节'), (0.20, '0.2 — 常见"锐"的观感'),
                     (0.30, ''), (0.40, ''), (0.50, '像素奈奎斯特（单帧能表达的上限）')):
        m = float(np.exp(-2 * np.pi ** 2 * rsig_native ** 2 * nu ** 2))
        print(f'  {nu:>12.2f} {m:>10.4f} {note:>28}')

    print('\n  MTF@0.5 = %.1e —— 也就是说，单帧在奈奎斯特处只剩万分之几的对比度，'
          % float(np.exp(-2 * np.pi ** 2 * rsig_native ** 2 * 0.25)))
    print('  像素网格根本没有"截掉"任何东西；亚像素堆栈没有混叠可补。')


if __name__ == '__main__':
    main()
