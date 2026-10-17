# -*- coding: utf-8 -*-
"""演示：视频 LUT 能不能直接套照片

三栏：原图 / 把 .cube 直接套在 sRGB 照片上 / 把同一套 look 移植到线性光后再编码
输出到 output/lut_photo_demo.jpg，并打印中间灰的映射。
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(r'D:\Software files\photo_watermark')
CUBE = Path(r'C:\Users\LQL\Pictures\raw\_lut\N-Log_to_Rec709_Standard.cube')

# --- 与 make_lut_standard.py 一致的 stage-2 look（作用在线性光上）---
LUM = np.array([0.2126, 0.7152, 0.0722], np.float32)
BLACK, EXPO, KNEE, CAP, Q, SAT, GATE = 0.012, 0.90, 0.75, 0.12, 3.0, 1.30, 0.060


def look(x):
    x = np.maximum(x - BLACK, 0.0) * EXPO
    over = x > KNEE
    x = np.where(over, KNEE + CAP * (x - KNEE) / (x - KNEE + Q), x)
    y = x @ LUM
    chroma = x.max(-1) - x.min(-1)
    t = np.clip(chroma / GATE, 0.0, 1.0)
    g = t * t * (3.0 - 2.0 * t)
    k = 1.0 + SAT * g
    return np.clip(y[..., None] + k[..., None] * (x - y[..., None]), 0.0, 1.0)


def srgb_to_lin(v):
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def lin_to_srgb(v):
    v = np.clip(v, 0, 1)
    return np.where(v <= 0.0031308, 12.92 * v, 1.055 * v ** (1 / 2.4) - 0.055)


def load_cube(path):
    size, vals = None, []
    with open(path) as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            if s.startswith('TITLE'):
                continue
            if s.startswith('LUT_3D_SIZE'):
                size = int(s.split()[1]); continue
            if s.startswith('DOMAIN_MIN') or s.startswith('DOMAIN_MAX'):
                continue
            vals.append([float(v) for v in s.split()])
    a = np.array(vals, np.float32).reshape(size, size, size, 3)   # [b,g,r]
    return a.transpose(2, 1, 0, 3)                                # -> [r,g,b]


def apply_cube(rgb, cube):
    """三线性插值，rgb 为 0..1 的...（沿用 cube 的输入域约定）"""
    n = cube.shape[0]
    c = np.clip(rgb, 0, 1) * (n - 1)
    i0 = np.floor(c).astype(np.int32)
    i1 = np.minimum(i0 + 1, n - 1)
    w = c - i0
    out = np.zeros_like(rgb, np.float32)
    for dr in (0, 1):
        wr = w[..., 0] if dr else 1 - w[..., 0]
        ir = i1[..., 0] if dr else i0[..., 0]
        for dg in (0, 1):
            wg = w[..., 1] if dg else 1 - w[..., 1]
            ig = i1[..., 1] if dg else i0[..., 1]
            for db in (0, 1):
                wb = w[..., 2] if db else 1 - w[..., 2]
                ib = i1[..., 2] if db else i0[..., 2]
                out += cube[ir, ig, ib] * (wr * wg * wb)[..., None]
    return out


def label(img, text):
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc', 26)
    d.text((12, 10), text, font=f, fill=(255, 255, 255),
           stroke_width=2, stroke_fill=(0, 0, 0))
    return img


def main():
    src = ROOT / 'input' / 'FUJIFILM.jpg'
    im = Image.open(src).convert('RGB')
    im.thumbnail((1400, 1400), Image.LANCZOS)
    arr = np.asarray(im, np.float32) / 255.0
    cube = load_cube(CUBE)
    print('cube', cube.shape)

    # 1) 直接套（输入被当成 N-Log）
    naive = np.clip(apply_cube(arr, cube), 0, 1)
    # 2) 移植到线性光：sRGB 解码 -> 同一个 look -> sRGB 编码
    lin = srgb_to_lin(arr)
    good = lin_to_srgb(look(lin))
    # 3) 反解做法：照片 -> N-Log 编码 -> 套 cube（等价于 2，只是精度损失）
    print('中灰 0.18 映射：')
    g = np.array([[[0.18, 0.18, 0.18]]], np.float32)
    print('  原图保持      ', g[0, 0])
    print('  直接套 cube   ', np.round(apply_cube(g, cube)[0, 0], 4))
    print('  look 移植线性 ', np.round(lin_to_srgb(look(srgb_to_lin(g)))[0, 0], 4))

    h, w = arr.shape[:2]
    gap = np.ones((h, 12, 3), np.uint8) * 255
    panels = [label(Image.fromarray((arr * 255).astype(np.uint8)), '原图'),
              label(Image.fromarray((naive * 255 + 0.5).astype(np.uint8)), '直接套 .cube（双重编码）'),
              label(Image.fromarray((good * 255 + 0.5).astype(np.uint8)), 'look 移植到线性光（正确）')]
    wide = np.concatenate([np.asarray(p) for p in panels] + [gap, gap], axis=1)
    out = ROOT / 'output' / 'lut_photo_demo.jpg'
    Image.fromarray(wide).save(out, quality=90)
    print('wrote', out, wide.shape)


if __name__ == '__main__':
    sys.exit(main())
