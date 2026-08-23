# -*- coding: utf-8 -*-
"""
从品牌 logo 图片中抠出文字/前景（去背景 → 透明 PNG）。

方法：
1. 取图片外圈（border ring）像素作为"背景色板"（取出现最多的前几色，
   兼容纯色背景、双色/渐变条纹背景，如 nikon 的黄底白纹）。
2. 每个像素到色板的最小 RGB 欧氏距离 d：
   d < T0 → 透明；d > T1 → 不透明；中间线性过渡（保留抗锯齿柔边）。
3. 与原有 alpha 通道取 min（已经带透明的 logo 直接受益）。
4. 按内容包围盒裁剪，输出 logo_texts/<品牌>_text.png。
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
LOGO_DIR = ROOT / 'logos'
OUT_DIR = ROOT / 'output' / 'logo_texts'

RADIUS = 10     # 边缘采样带宽（像素）
T0, T1 = 18.0, 70.0  # 透明/不透明距离阈值（RGB 欧氏距离）
ALPHA_CUTOFF = 30    # 低于此 alpha 判为背景残留（去雾）
RING_MIN_FREQ = 0.005  # 色板收录频率下限（0.5%）：收录边缘出现过的所有颜色，
# 渐变背景在边缘处必然覆盖全部渐变值 → 内部渐变像素都能匹配上
PAD = 8         # 输出留白（像素）


def extract_logo_text(path: Path) -> Path:
    im = Image.open(path).convert('RGBA')
    w, h = im.size
    arr = np.asarray(im, dtype=np.float64)

    # --- 1) 边缘色板（收录边缘出现的所有常见颜色，兼容渐变/条纹背景）---
    ring = np.concatenate([
        arr[:RADIUS].reshape(-1, 4),
        arr[-RADIUS:].reshape(-1, 4),
        arr[:, :RADIUS].reshape(-1, 4),
        arr[:, -RADIUS:].reshape(-1, 4),
    ])
    # 边缘环含明显透明像素 → 该图自带透明背景（含"透明镂空"设计，如 xiaomi）→ 直接信任原 alpha
    if ring[:, 3].mean() < 200:
        alpha = arr[:, :, 3].copy()
    else:
        # 透明像素不参与色板（否则图标型 logo 的边缘会把自己的颜色学进去）
        opaque = ring[ring[:, 3] > 128, :3]
        # 量化到 16 级（细粒度，覆盖渐变带）
        q = (opaque // 16 * 16 + 8).astype(np.int32)
        uniq, counts = np.unique(q, axis=0, return_counts=True)
        keep = counts / len(opaque) > RING_MIN_FREQ
        palette = [uniq[i] for i in np.where(keep)[0]]
        if not palette:  # 兜底：用出现最多的颜色
            palette = [uniq[int(np.argmax(counts))]]

        # --- 2) 距离 → 软 alpha ---
        pal = np.array(palette, dtype=np.float64)      # (k,3)
        rgb = arr[:, :, :3]                            # (h,w,3)
        d = np.min(np.sqrt(((rgb[:, :, None, :] - pal[None, None, :, :]) ** 2).sum(axis=-1)), axis=-1)
        alpha = np.clip((d - T0) / (T1 - T0) * 255.0, 0, 255)
        # 与原 alpha 合成
        alpha = np.minimum(alpha, arr[:, :, 3])
        # 去雾：低 alpha = 背景残留
        alpha[alpha < ALPHA_CUTOFF] = 0
        # 形态学开运算（先腐蚀后膨胀）：去掉细线状杂质（条纹边缘残留）
        a_img = Image.fromarray(alpha.astype(np.uint8), 'L')
        a_img = a_img.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
        alpha = np.asarray(a_img)
    out = np.dstack([arr[:, :, :3], alpha])

    # --- 3) 裁剪到内容 ---
    mask = out[:, :, 3] > 8
    if not mask.any():
        raise RuntimeError(f'{path.name}: 未检测到前景内容')
    ys, xs = np.where(mask)
    y0, y1 = max(ys.min() - PAD, 0), min(ys.max() + PAD, h)
    x0, x1 = max(xs.min() - PAD, 0), min(xs.max() + PAD, w)
    result = Image.fromarray(out.astype(np.uint8), 'RGBA').crop((x0, y0, x1, y1))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f'{path.stem}_text.png'
    result.save(out_path)
    return out_path


def main():
    files = sorted(LOGO_DIR.glob('*.png'))
    ok, fail = [], []
    for p in files:
        try:
            out = extract_logo_text(p)
            im = Image.open(out)
            ok.append((p.name, out.name, im.size,
                       len(np.unique(np.asarray(im.getchannel('A'))))))
            print(f'  OK  {p.name:26s} -> {im.size[0]}x{im.size[1]}  alpha级数={ok[-1][3]}')
        except Exception as e:
            fail.append(p.name)
            print(f' FAIL {p.name:26s} {e}')
    print(f'\n完成 {len(ok)} 个，失败 {len(fail)} 个: {fail}')


if __name__ == '__main__':
    sys.exit(main())
