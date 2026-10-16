# -*- coding: utf-8 -*-
"""超分输出质量对比工具：原图 vs 生成图 的色彩参数 + 细节指数

指标口径沿用 docs/super_res/算法升级记录.md（真实照片验证）：
- 锐度/细节：拉普拉斯方差；梯度平均作为细节指数
- 噪声：中值滤波残差 std
- 色彩：RGB 中位数与 R/G、B/G 比率；两图通道比率（生成/参考）直读偏色
同区域同尺度：两图都取中心区域、缩放到同一宽度再算。

参考图支持 RAW（按旧管线观感渲染：sRGB 解码+gamma+0.5-99.5 拉伸）或普通图片；
生成图支持 JPG/TIF/DNG（DNG 读数据域，需另给参考时同口径线性比较省略，只测结构）。

用法:
  python devtests/sr_compare.py                                    # 默认 Z6 测试对
  python devtests/sr_compare.py <参考RAW或图片> <生成JPG/TIF>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import cv2

DEFAULT_REF = r'C:\Users\LQL\Pictures\raw\100NCZ_6\DSC_0020.NEF'
DEFAULT_OUT = r'C:\Users\LQL\Pictures\raw\100NCZ_6\SR输出\dng测试3_adobe结构_2x.jpg'
W = 1200  # 统一比较宽度


def imread_rgb(path):
    """中文路径安全读图"""
    buf = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f'无法读取: {path}')
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def render_reference(raw_path):
    """RAW -> 旧管线观感（sRGB + gamma2.2 + 0.5-99.5 拉伸），与此前认可的观感一致"""
    import sr_process
    img = sr_process.read_raw_full(raw_path)
    img = np.clip(img, 0, 1) ** (1 / 2.2)
    for c in range(3):
        ch = img[:, :, c]
        lo, hi = np.percentile(ch, 0.5), np.percentile(ch, 99.5)
        img[:, :, c] = np.clip((ch - lo) / (hi - lo + 1e-8), 0, 1)
    return (img * 255).astype(np.uint8)


def center_crop(img, frac=0.6):
    h, w = img.shape[:2]
    ch, cw = int(h * frac), int(w * frac)
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    return img[y0:y0 + ch, x0:x0 + cw]


def resize_w(img, width=W):
    h = int(img.shape[0] * width / img.shape[1])
    return cv2.resize(img, (width, h), interpolation=cv2.INTER_AREA)


def color_stats(name, img):
    m = np.median(img.reshape(-1, 3), axis=0)
    print(f'{name}')
    print(f'  色彩  中位数 RGB = {m.round(1)}   R/G = {m[0]/max(m[1],1):.2f}   B/G = {m[2]/max(m[1],1):.2f}')
    return m


def detail_stats(name, img):
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY).astype(np.float64)
    sharp = cv2.Laplacian(g, cv2.CV_64F).var()
    grad = np.abs(cv2.Laplacian(g, cv2.CV_64F)).mean()
    med = cv2.medianBlur(g.astype(np.uint8), 3).astype(np.float64)
    noise = (g - med).std()
    print(f'  细节  拉普拉斯方差 = {sharp:8.0f}   梯度平均 = {grad:6.2f}   中值残差std = {noise:.2f}')
    return sharp, grad, noise


def main():
    ref_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(DEFAULT_REF)
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(DEFAULT_OUT)

    ref = render_reference(ref_path) if ref_path.suffix.lower() in (
        '.nef', '.nrw', '.arw', '.cr2', '.cr3', '.orf', '.raf', '.rw2') else imread_rgb(ref_path)
    out = imread_rgb(out_path) if out_path.suffix.lower() != '.dng' else None
    if out is None:
        import tifffile
        arr = tifffile.imread(str(out_path)).astype(np.float64) / 65535.0
        out = (np.clip(arr ** (1 / 2.2), 0, 1) * 255).astype(np.uint8)

    ref_c = resize_w(center_crop(ref))
    out_c = resize_w(center_crop(out))

    print(f'参考: {ref_path.name}  vs  生成: {out_path.name}   （中心60%区域，统一 {W}px 宽）')
    print('-' * 72)
    m1 = color_stats('参考', ref_c)
    m2 = color_stats('生成', out_c)
    ratio = m2 / np.maximum(m1, 1)
    print(f'  偏色  生成/参考 通道比率 = R:{ratio[0]:.2f}  G:{ratio[1]:.2f}  B:{ratio[2]:.2f}'
          f'   （三个都≈1 为无偏色）')
    print('-' * 72)
    detail_stats('参考', ref_c)
    detail_stats('生成', out_c)


if __name__ == '__main__':
    main()
