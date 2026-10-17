# -*- coding: utf-8 -*-
"""JSR（Stormer K7，7 帧，2x）与本管线 9 帧 2x 的同条件对比

JSR 的 DNG 是 camera-linear（AsShotNeutral + ColorMatrix1），按标准公式手动
develop；两边都归到 2x 同一尺度、同一色调映射（盘内中位→0.5）。
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
CROP = Path(r'C:\Users\LQL\AppData\Local\Temp\jsr_moon_crop.npy')

ASN = np.array([0.655570, 1.0, 0.652229])
CM = np.array([[0.9943, -0.3269, -0.0839],
               [-0.5323, 1.3269, 0.2259],
               [-0.1198, 0.2083, 0.7557]])
XYZ2SRGB = np.array([[3.2406, -1.5372, -0.4986],
                     [-0.9689, 1.8758, 0.0415],
                     [0.0557, -0.2040, 1.0570]])


def develop_jsr(crop_u16):
    """camera-linear -> 白平衡后的相机 RGB（不做矩阵，只用于亮度对比）

    手搓 DNG 色彩矩阵容易把色度掰歪（实测偏青/偏品红），而这里要比较的是
    细节/锐度——亮度就够，且月面本身是中性命灰。
    """
    lin = crop_u16.astype(np.float64) / 65535.0
    lin = lin / ASN
    return np.clip(lin, 0, 1).astype(np.float32)


def label(img_u8, text):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.fromarray(cv2.cvtColor(img_u8, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(im)
    font = None
    for fp in (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf'):
        if Path(fp).exists():
            font = ImageFont.truetype(fp, 20)
            break
    d.text((8, 6), text, font=font, fill=(255, 255, 255), stroke_width=2,
           stroke_fill=(0, 0, 0))
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


def find_disk(img):
    g = img @ np.array([.2126, .7152, .0722], np.float32)
    d = sr_moon.detect_bright_subject(g)
    return d


def render(img, r_px, gamma=2.2, target=0.5, gray=True):
    """按盘内中位定标 + gamma；gray=True 时输出三通道相同的灰度"""
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    m = ((xx - w / 2) ** 2 + (yy - h / 2) ** 2) <= (0.8 * r_px) ** 2
    if gray:
        lum = img @ np.array([.2126, .7152, .0722], np.float32)
        med = float(np.median(lum[m]))
        k = (target ** gamma) / max(med, 1e-6)
        g = np.power(np.clip(lum * k, 0, 1), 1 / gamma)
        return np.repeat(g[..., None], 3, axis=2)
    lum = img @ np.array([.2126, .7152, .0722], np.float32)
    med = float(np.median(lum[m]))
    k = (target ** gamma) / max(med, 1e-6)
    out = np.power(np.clip(img * k, 0, 1), 1 / gamma)
    return np.clip(out, 0, 1)


def main():
    jsr_u16 = np.load(str(CROP))
    jsr = develop_jsr(jsr_u16)
    # DNG 的色彩矩阵/白平衡手搓容易偏色（JSR 自己的开发链有额外标定）：
    # 月面是中性命灰，直接按盘内中位做灰世界，保证比较的是细节而不是偏色
    h, w = jsr.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    rr = np.sqrt((xx - w / 2) ** 2 + (yy - h / 2) ** 2)
    dmask = rr < 0.8 * 320
    meds = np.array([np.median(jsr[..., k][dmask]) for k in range(3)])
    jsr = jsr * (meds.mean() / np.maximum(meds, 1e-6))[None, None, :]
    jsr = np.clip(jsr, 0, 1).astype(np.float32)
    dj = dict(cx=jsr.shape[1] / 2.0, cy=jsr.shape[0] / 2.0, r=320.0)

    # 我的母版：走与产品一致的成品链（RL 去卷积 + 小波）
    stack = sr_process.load_subifd_rgb(str(OUT / '月亮_9帧堆栈_2x.dng')).astype(np.float32) / 65535.0
    H, W = stack.shape[:2]
    c = (W / 2.0, H / 2.0)
    r_s = 316.0
    sig, _edge, _q = sr_moon.estimate_psf_sigma(stack.mean(2), c[0], c[1], r_s)
    dec = sr_process.rl_deconvolve_u16((np.clip(stack, 0, 1) * 65535).astype(np.uint16),
                                       iterations=24, sigma=float(np.clip(sig, 1.6, 6.0)))
    dec = dec.astype(np.float32) / 65535.0
    A, b = sr_process.derive_camera_to_srgb(SRC / 'DSC_9089.NEF')
    mine = np.clip(dec.astype(np.float64) @ np.asarray(A).T + np.asarray(b), 0, 1).astype(np.float32)
    yy2, xx2 = np.mgrid[0:H, 0:W]
    mmask = np.clip((r_s * 0.94 - np.sqrt((xx2 - c[0]) ** 2 + (yy2 - c[1]) ** 2)) / (r_s * 0.05),
                    0, 1).astype(np.float32)
    mine = np.clip(sr_moon.wavelet_sharpen(mine, gains=sr_process.SUBJECT_WAVELET_GAINS,
                                           local_contrast=sr_process.SUBJECT_LOCAL_CONTRAST,
                                           mask=mmask), 0, 1).astype(np.float32)
    dm = dict(cx=mine.shape[1] / 2.0, cy=mine.shape[0] / 2.0, r=316.0)
    print('JSR', jsr.shape, '本管线', mine.shape)

    # 统一窗口：以各自盘心为中心，取 760x760（2x 像素）
    W = 760
    def cut(img, d):
        cx, cy = int(round(d['cx'])), int(round(d['cy']))
        h, w = img.shape[:2]
        y0 = max(0, min(h - W, cy - W // 2)); x0 = max(0, min(w - W, cx - W // 2))
        return img[y0:y0 + W, x0:x0 + W]
    jc, mc = cut(jsr, dj), cut(mine, dm)
    print('窗口', jc.shape, mc.shape)

    jr = render(jc, dj['r'])
    mr = render(mc, dm['r'])
    for name, im in (('JSR', jr), ('本管线', mr)):
        h, w = im.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w]
        m = ((xx - w / 2) ** 2 + (yy - h / 2) ** 2) <= (0.8 * 300) ** 2
        lum = im @ np.array([.2126, .7152, .0722], np.float32)
        print(f'{name}: 中位 {float(np.median(lum[m])):.3f} 贴顶 {(lum >= .995).mean()*100:.3f}% '
              f'峰值 {float(lum.max()):.3f}')

    L = label(cv2.cvtColor((jr * 255 + .5).astype(np.uint8), cv2.COLOR_RGB2BGR),
              'JSR Stormer K7（7帧，2x）')
    R = label(cv2.cvtColor((mr * 255 + .5).astype(np.uint8), cv2.COLOR_RGB2BGR),
              '本管线（9帧，2x）')
    pair = np.concatenate([L, np.full((W, 8, 3), 255, np.uint8), R], axis=1)
    ok, buf = cv2.imencode('.png', pair)
    (OUT / '对比_JSR_vs_本管线.png').write_bytes(buf.tobytes())

    cw = 190
    x0 = W // 2 - cw // 2
    y0 = W // 2 + 30
    z = np.concatenate([L[y0:y0 + cw, x0:x0 + cw], np.full((cw, 6, 3), 255, np.uint8),
                        R[y0:y0 + cw, x0:x0 + cw]], axis=1)
    z = cv2.resize(z, (z.shape[1] * 4, z.shape[0] * 4), interpolation=cv2.INTER_NEAREST)
    ok, buf = cv2.imencode('.png', z)
    (OUT / '对比_JSR_vs_本管线_细节.png').write_bytes(buf.tobytes())
    print('wrote', pair.shape, z.shape)


if __name__ == '__main__':
    main()
