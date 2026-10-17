# -*- coding: utf-8 -*-
"""月亮成品观感调参：RL 之后加多尺度小波锐化，出对比图选参数"""
import sys
from pathlib import Path

import numpy as np
import cv2

sys.path.insert(0, r'D:\Software files\photo_watermark')
import sr_moon
import sr_process

SRC = Path(r'C:\Users\LQL\Pictures\raw\月亮')
OUT = SRC / 'SR输出'


def label(img_u8, text, org=(8, 26)):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.fromarray(cv2.cvtColor(img_u8, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(im)
    font = None
    for fp in (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf'):
        if Path(fp).exists():
            font = ImageFont.truetype(fp, 20)
            break
    d.text(org, text, font=font, fill=(255, 255, 255), stroke_width=2,
           stroke_fill=(0, 0, 0))
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


def main():
    stack = sr_process.load_subifd_rgb(str(OUT / '月亮_9帧堆栈_2x.dng')).astype(np.float32) / 65535.0
    H, W = stack.shape[:2]
    A, b = sr_process.derive_camera_to_srgb(SRC / 'DSC_9089.NEF')
    c = (W / 2.0, H / 2.0)
    r_s = 158 * 2
    sig, edge, _ = sr_moon.estimate_psf_sigma(stack.mean(axis=2), c[0], c[1], r_s)
    sigma = float(np.clip(sig, 1.6, 6.0))
    dec = sr_process.rl_deconvolve_u16(
        (np.clip(stack, 0, 1) * 65535).astype(np.uint16), iterations=24,
        sigma=sigma).astype(np.float32) / 65535.0
    srgb = np.clip(dec.astype(np.float64) @ np.asarray(A).T + b, 0, 1).astype(np.float32)
    print('sigma', sigma, 'edge', edge)

    variants = [
        ('A 现状（无小波）', None),
        ('B 小波 轻', dict(gains=(0.5, 1.4, 1.2, 1.0))),
        ('C 小波+局部对比', dict(gains=(0.6, 1.7, 1.4, 1.1), local_contrast=0.10)),
        ('D 小波 强', dict(gains=(0.7, 2.0, 1.6, 1.2), local_contrast=0.15)),
    ]
    # 羽化月盘掩膜（盘缘外不锐化，避免小波黑边）
    yy, xx = np.mgrid[0:H, 0:W]
    rr = np.sqrt((xx - c[0]) ** 2 + (yy - c[1]) ** 2)
    mask = np.clip((r_s * 0.99 - rr) / (r_s * 0.04), 0, 1).astype(np.float32)
    panels = []
    for name, kw in variants:
        img = srgb if kw is None else sr_moon.wavelet_sharpen(srgb, mask=mask, **kw)
        out = sr_moon.subject_tonemap(np.clip(img, 0, 1), r_s * 0.95, center=c)
        u8 = cv2.cvtColor((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8), cv2.COLOR_RGB2BGR)
        panels.append((name, u8))
        disk = rr < r_s * 0.8
        print(f'{name}: 盘内中位 {float(np.median(out[disk])):.3f} '
              f'贴顶 {(out >= 0.995).mean()*100:.2f}%')
    # 全盘 + 局部放大两行
    gap = np.full((panels[0][1].shape[0], 8, 3), 255, np.uint8)
    row = []
    for name, u8 in panels:
        row.append(label(u8.copy(), name))
        row.append(gap)
    full = np.concatenate(row[:-1], axis=1)
    ok, buf = cv2.imencode('.png', full)
    (OUT / '调参_全盘.png').write_bytes(buf.tobytes())

    cw = 300
    x0, y0 = int(c[0] - 60), int(c[1] + 40)
    gap2 = np.full((cw * 2, 8, 3), 255, np.uint8)
    row2 = []
    for name, u8 in panels:
        crop = u8[y0:y0 + cw, x0:x0 + cw]
        crop = cv2.resize(crop, (cw * 2, cw * 2), interpolation=cv2.INTER_NEAREST)
        row2.append(label(crop, name))
        row2.append(gap2)
    detail = np.concatenate(row2[:-1], axis=1)
    ok, buf = cv2.imencode('.png', detail)
    (OUT / '调参_细节.png').write_bytes(buf.tobytes())
    print('wrote', full.shape, detail.shape)


if __name__ == '__main__':
    main()
