# -*- coding: utf-8 -*-
"""月亮任务最终产物：产品管线出 DNG/JPG + 单帧/堆栈同条件对比图

产物（C:\\Users\\LQL\\Pictures\\raw\\月亮\\SR输出\\）:
    月亮_9帧堆栈_2x.dng / .jpg
    月亮_对比_单帧vs堆栈_2x.png
"""
import sys
import time
from pathlib import Path

import numpy as np
import cv2

sys.path.insert(0, r'D:\Software files\photo_watermark')
import sr_process
import sr_moon
from align import read_raw_as_gray

SRC = Path(r'C:\Users\LQL\Pictures\raw\月亮')
OUT = SRC / 'SR输出'
ROI_OUT = OUT / '月亮ROI'


def put_label(img_u8, text, org=(10, 28), color=(255, 255, 255)):
    try:
        from PIL import Image, ImageDraw, ImageFont
        im = Image.fromarray(cv2.cvtColor(img_u8, cv2.COLOR_BGR2RGB))
        d = ImageDraw.Draw(im)
        font = None
        for fp in (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf'):
            if Path(fp).exists():
                font = ImageFont.truetype(fp, 22)
                break
        d.text(org, text, font=font, fill=(255, 255, 255), stroke_width=2,
               stroke_fill=(0, 0, 0))
        return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
    except Exception:
        cv2.putText(img_u8, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        return img_u8


def main():
    files = sorted(SRC.glob('*.NEF'))
    out_base = OUT / '月亮_9帧堆栈'
    t0 = time.time()
    sr_process.process(files=[str(p) for p in files],
                       output_path=str(out_base) + '.tif', scale=2,
                       max_frames=16, threads=8)
    print(f'[产品管线] {time.time()-t0:.1f}s')

    dng = Path(str(out_base) + '_2x.dng')
    jpg = Path(str(out_base) + '_2x.jpg')
    stack = sr_process.load_subifd_rgb(str(dng)).astype(np.float32) / 65535.0
    H, W = stack.shape[:2]
    scale = 2.0

    # 单帧侧：最锐帧（DSC_9089）裁出与母版同尺寸的原生窗口（母版 = 原生 W/2）
    nW = int(round(W / scale))
    ref_p = SRC / 'DSC_9089.NEF'
    g = read_raw_as_gray(ref_p, half_size=True)
    d = sr_moon.detect_bright_subject(g)
    cx, cy = d['cx'] * 2.0, d['cy'] * 2.0
    img = sr_process.read_raw_camera_linear(ref_p)
    x0, y0 = int(round(cx - nW / 2.0)), int(round(cy - nW / 2.0))
    single = img[y0:y0 + nW, x0:x0 + nW].copy()
    del img
    single2x = cv2.resize(single, (W, H), interpolation=cv2.INTER_CUBIC)

    # 同一去卷积参数（σ 由堆栈盘缘实测）
    A, b = sr_process.derive_camera_to_srgb(ref_p)
    r_s = d['r'] * 2.0 * scale
    cx_s, cy_s = W / 2.0, H / 2.0
    sig, edge, _q = sr_moon.estimate_psf_sigma(stack.mean(axis=2), cx_s, cy_s, r_s)
    sigma_sr = float(np.clip(sig, 0.8 * scale, 3.0 * scale))
    iters = 24
    print(f'σ={sigma_sr:.2f}px(SR) iters={iters}')

    yy, xx = np.mgrid[0:H, 0:W]
    rr = np.sqrt((xx - cx_s) ** 2 + (yy - cy_s) ** 2)
    wmask = np.clip((r_s * 0.94 - rr) / (r_s * 0.05), 0, 1).astype(np.float32)

    def render(lin, enhance=True):
        srgb = np.clip(lin.astype(np.float64) @ np.asarray(A).T + np.asarray(b), 0, 1).astype(np.float32)
        if enhance:
            srgb = np.clip(sr_moon.wavelet_sharpen(
                srgb, gains=sr_process.SUBJECT_WAVELET_GAINS,
                local_contrast=sr_process.SUBJECT_LOCAL_CONTRAST, mask=wmask), 0, 1)
        return sr_moon.subject_tonemap(srgb, r_s * 0.95, center=(cx_s, cy_s))

    def dec(x):
        return sr_process.rl_deconvolve_u16(
            (np.clip(x, 0, 1) * 65535).astype(np.uint16),
            iterations=iters, sigma=sigma_sr).astype(np.float32) / 65535.0

    single_r = render(dec(single2x), enhance=False)
    stack_r = render(dec(stack), enhance=False)
    stack_final = render(dec(stack), enhance=True)

    def to_u8(x):
        return cv2.cvtColor((np.clip(x, 0, 1) * 255 + 0.5).astype(np.uint8), cv2.COLOR_RGB2BGR)

    # 公平对比只用"物理链"（去卷积）——小波提亮会把单帧噪声也一起放大，
    # 那样比的是"谁噪声多"，不是分辨率。成品（含小波）单列一张。
    a = put_label(to_u8(single_r), '单帧（最锐 1/9）+ 去卷积')
    bb = put_label(to_u8(stack_r), '9 帧堆栈 + 去卷积')
    gap = np.full((H, 10, 3), 255, np.uint8)
    pair = np.concatenate([a, gap, bb], axis=1)
    ok, buf = cv2.imencode('.png', pair)
    (OUT / '月亮_对比_单帧vs堆栈_2x.png').write_bytes(buf.tobytes())
    print('对比图', pair.shape)
    ok, buf = cv2.imencode('.png', put_label(to_u8(stack_final),
                                             '9 帧成品（去卷积 + 多尺度小波）'))
    (OUT / '月亮_成品_2x.png').write_bytes(buf.tobytes())

    # 细节三联（2x 最近邻放大，看环形山边缘）
    cw = 220
    x1, y1 = int(cx_s - 40), int(cy_s + 60)
    insets = []
    for lab, im in [('单帧+RL', single_r), ('堆栈+RL', stack_r), ('堆栈成品', stack_final)]:
        c = to_u8(im[y1:y1 + cw, x1:x1 + cw])
        c = cv2.resize(c, (cw * 2, cw * 2), interpolation=cv2.INTER_NEAREST)
        insets.append(put_label(c, lab, (8, 26)))
        insets.append(np.full((cw * 2, 8, 3), 255, np.uint8))
    z = np.concatenate(insets[:-1], axis=1)
    ok, buf = cv2.imencode('.png', z)
    (OUT / '月亮_对比_细节.png').write_bytes(buf.tobytes())
    print('细节图', z.shape)

    # 客观量（写报告用）
    def hp(img, s=3.0):
        return float(np.std(img - cv2.GaussianBlur(img, (0, 0), s)))
    yy, xx = np.mgrid[0:H, 0:W]
    disk = ((xx - cx_s) ** 2 + (yy - cy_s) ** 2) < (0.8 * r_s) ** 2
    patch = (slice(8, 80), slice(8, 80))
    print(f'天空噪声 单帧 {hp(single.mean(axis=2)[patch]):.5f} '
          f'→ 堆栈 {hp(stack.mean(axis=2)[patch]):.5f}')
    def hpm(img, mask):
        return float(np.std((img - cv2.GaussianBlur(img, (0, 0), 3.0))[mask]))
    print(f'盘内高频 单帧 {hpm(single2x.mean(axis=2), disk):.5f} '
          f'→ 堆栈 {hpm(stack.mean(axis=2), disk):.5f}')
    print('clip 比例 单帧', float((single_r >= 0.995).mean()),
          '堆栈', float((stack_r >= 0.995).mean()))
    print(f'总耗时 {time.time()-t0:.1f}s')


if __name__ == '__main__':
    main()
