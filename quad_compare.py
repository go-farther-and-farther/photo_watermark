#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""四格对比图生成：同一裁切、同一色调下并排比较多帧合成 / 少帧合成 / 单帧

用途：回答"多拍的那 21 帧值不值"。把全帧数合成（参考）、top-N 帧合成、
最锐单帧、中位帧的同一位置裁切拼成一张 2x2 网格，色调对齐后剩下的差别
就只有噪声与锐度，直接看图说话。

三条实测铁律（2026-10-25 夜景批次验证，改动前先想清楚）：

1. **每个裁切块单独做局部对齐**。手持连拍帧间有 0.1–1° 整机旋转，整图
   平移配准在画面角落会差几十像素；用裁切块自身（模糊 + 降采样后）当
   模板在全图里 matchTemplate 才贴得住。
2. **色调对齐只用"增益 + gamma"两个参数**（分别拟合模糊亮度图的 p99 与
   中位）。不能用自由直方图 LUT：暗部彩色噪点会被整段拉开，出来是黄紫
   斑块；两参数是全通道同一条单调曲线，色相不会跳。
3. **单帧要走与合成相同的渲染管线**（把同一帧复制多份喂 sr_process），
   否则色调不可比——本模块只负责拼图，不管面板图怎么来。

典型用法（更顺手的入口是主界面「📐 四格对比」窗口）：

    py quad_compare.py --ref 夜景_合成_2x.jpg ^
        --panel "8 帧合成=top8_2x.jpg" ^
        --panel "最锐单帧=最锐_2x.jpg" --panel "中位帧=中位_2x.jpg" ^
        --spot 自行车=0.783,0.820 --spot 路灯树冠=0.620,0.750 ^
        --out "C:\\Users\\...\\SR对比图\\批次名"

面板顺序即画布顺序，参考图固定排在最后一个（右下角）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

# BGR 顺序的 BT.709 亮度权重（OpenCV 读图是 BGR）
LUMA_BGR = np.array([0.0722, 0.7152, 0.2126], np.float32)

DEFAULT_CROP = (1000, 680)
DEFAULT_FONT = r'C:\Windows\Fonts\msyh.ttc'
DEFAULT_TITLE = '{label}　同一裁切、色调已对齐，差别只剩噪声'
DEFAULT_PREFIX = '对比_四格_'
# 对齐得分低于它说明这一块没贴住（空天/纯色区域常见），日志里给个警示
ALIGN_WARN = 0.35


# ---------------------------------------------------------------- 基础 IO

def imread_color(path) -> np.ndarray:
    """读图为 BGR uint8。OpenCV 在 Windows 下读不了中文路径，一律走字节流。"""
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise IOError(f'无法解码图片: {path}')
    return img


def _gray(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


# ---------------------------------------------------------------- 局部对齐

def local_align(ref_crop: np.ndarray, img_gray: np.ndarray,
                down: int = 4, blur: float = 4.0):
    """在整图灰度里找裁块对应的位置，返回 (x, y, score)

    裁块与全图都先高斯模糊再 down 倍降采样：抑制噪点与压缩伪影、把搜索
    量降 down² 倍；模板匹配命中后坐标 × down 回到全图分辨率（精度即
    down px，对"看噪声差别"的对比图足够）。裁块比图还大时报错。
    """
    a = cv2.GaussianBlur(_gray(ref_crop), (0, 0), blur)[::down, ::down]
    b = cv2.GaussianBlur(img_gray, (0, 0), blur)[::down, ::down]
    if a.shape[0] > b.shape[0] or a.shape[1] > b.shape[1]:
        raise ValueError('裁切块比目标图还大，无法对齐')
    res = cv2.matchTemplate(b, a, cv2.TM_CCOEFF_NORMED)
    _, sc, _, loc = cv2.minMaxLoc(res)
    return loc[0] * down, loc[1] * down, float(sc)


# ---------------------------------------------------------------- 色调对齐

def match_tone(crop: np.ndarray, ref: np.ndarray, blur: float = 8.0) -> np.ndarray:
    """把 crop 的色调拉到 ref：增益 k 定 p99，gamma 定中位（uint8 BGR in/out）

    两参数都是全通道同一条单调曲线——这是与自由 LUT 的本质区别：暗部
    彩色噪点不会被各自拉开成黄紫斑块。gamma 由"增益后中位 → 参考中位"
    解析求得，clip 在 0.4~2.5 防极端值。
    """
    if crop.shape != ref.shape:
        raise ValueError(f'色调对齐要求两块同尺寸: {crop.shape[:2]} vs {ref.shape[:2]}')
    cb = cv2.GaussianBlur(crop, (0, 0), blur).astype(np.float32)
    rb = cv2.GaussianBlur(ref, (0, 0), blur).astype(np.float32)
    cl = cb @ LUMA_BGR
    rl = rb @ LUMA_BGR
    cp99, rp99 = float(np.percentile(cl, 99)), float(np.percentile(rl, 99))
    k = rp99 / max(cp99, 1e-3)
    cm = float(np.median(cl)) * k
    rm = float(np.median(rl))
    if cm > 1.0 and rm > 1.0:
        g = float(np.log(rm / 255.0) / np.log(cm / 255.0))
        # 常数图会得到 0/0=NaN（如全 128 的 crop 对纯白参考），clip 拦不住
        # NaN，一路 power 出平台相关的垃圾值——按"无 gamma 可解"退回 1.0
        g = float(np.clip(g, 0.4, 2.5)) if np.isfinite(g) else 1.0
    else:
        g = 1.0
    out = np.power(np.clip(crop.astype(np.float32) * k / 255.0, 0, 1), g) * 255.0
    return np.clip(out, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- 拼画布

def _load_font(path: str | None, px: int):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(path or DEFAULT_FONT, px)
    except Exception:                                     # 无雅黑时退默认字体（中文会变方块）
        return ImageFont.load_default()


def compose_grid(panels, title: str = '', font_path: str | None = None,
                 pad: int = 18, top: int = 58, lab: int = 34, bot: int = 44):
    """把 [(标签, BGR ndarray)] 拼成 2 列深色网格画布（4 格即 2x2），返回 PIL Image

    标签在每格上方（暖黄），标题横贯顶部；行数 = ceil(面板数/2)。
    """
    from PIL import Image, ImageDraw
    panels = [(str(nm), np.asarray(c)) for nm, c in panels]
    if not panels:
        raise ValueError('没有可拼的面板')
    ch, cw = panels[0][1].shape[:2]
    for nm, c in panels:
        if c.shape[0] != ch or c.shape[1] != cw:
            raise ValueError(f'面板尺寸不一致: "{nm}" {c.shape[1]}x{c.shape[0]} '
                             f'vs 首块 {cw}x{ch}')
    rows = (len(panels) + 1) // 2
    canvas = Image.new('RGB', (cw * 2 + pad * 3, ch * rows + top + lab * rows + bot + pad),
                       (14, 14, 16))
    d = ImageDraw.Draw(canvas)
    f_title = _load_font(font_path, 25)
    f_lab = _load_font(font_path, 21)
    if title:
        d.text((pad, 14), title, font=f_title, fill=(238, 238, 238))
    for i, (nm, c) in enumerate(panels):
        r, col = divmod(i, 2)
        x = pad + col * (cw + pad)
        y = top + r * (ch + lab) + lab
        d.text((x, y - 26), nm, font=f_lab, fill=(255, 205, 120))
        canvas.paste(Image.fromarray(cv2.cvtColor(c, cv2.COLOR_BGR2RGB)), (x, y))
    return canvas


# ---------------------------------------------------------------- 编排

def generate_comparisons(ref_path, panel_specs, spots, out_dir,
                         crop_wh=DEFAULT_CROP, title_fmt: str = DEFAULT_TITLE,
                         font_path: str | None = None, prefix: str = DEFAULT_PREFIX,
                         ref_label: str | None = None,
                         on_progress=None, verbose: bool = True) -> list[Path]:
    """逐裁切点生成对比图，返回输出文件列表

    ref_path:     参考图（通常为全帧数合成成品 JPG/TIF），裁切与色调都以它为准
    panel_specs:  [(标签, 路径)] 对比面板，顺序即画布顺序；参考图排最后（右下）
    spots:        [(点名, fx, fy)] 相对坐标 0~1，参考图坐标系
    out_dir:      输出目录（自动创建），文件名 = prefix + 点名 + .png
    on_progress(done, total, name, out_path)：每个点回一次（失败点 out_path=None）
    单点失败不中断整批：日志记原因后继续，最终只返回成功的。
    """
    ref = imread_color(ref_path)
    ref_g = _gray(ref)
    H, W = ref.shape[:2]
    cw, ch = int(crop_wh[0]), int(crop_wh[1])
    if cw > W or ch > H:
        raise ValueError(f'裁切尺寸 {cw}x{ch} 比参考图 {W}x{H} 还大')

    imgs = []
    for label, p in panel_specs:
        im = imread_color(p)
        if im.shape[1] < cw or im.shape[0] < ch:
            raise ValueError(f'面板"{label}"的图 {im.shape[1]}x{im.shape[0]} '
                             f'比裁块 {cw}x{ch} 还小，裁不出可比的块')
        imgs.append((str(label), im, _gray(im)))
    if not imgs:
        raise ValueError('至少要给一个对比面板（--panel）')
    if ref_label is None:
        ref_label = Path(ref_path).stem

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    outs: list[Path] = []
    total = len(spots)
    for i, (name, fx, fy) in enumerate(spots, 1):
        out = None
        try:
            x0 = max(0, min(int(W * fx) - cw // 2, W - cw))
            y0 = max(0, min(int(H * fy) - ch // 2, H - ch))
            ref_crop = ref[y0:y0 + ch, x0:x0 + cw]

            panels, notes = [], []
            for label, im, g in imgs:
                dx, dy, sc = local_align(ref_crop, g)
                # 降采样取整可能让裁块越界一两像素，按面板图自身尺寸钳回
                dx = max(0, min(dx, im.shape[1] - cw))
                dy = max(0, min(dy, im.shape[0] - ch))
                panels.append((label, match_tone(im[dy:dy + ch, dx:dx + cw], ref_crop)))
                # 纯色/天空等零方差模板会让 TM_CCOEFF_NORMED 给出 NaN，
                # NaN < 阈值 恒 False 会漏掉警示——用 not(sc >= 阈值) 把 NaN 也算未贴住
                notes.append(f'{label} {sc:.3f}'
                             + ('（未贴住!）' if not (sc >= ALIGN_WARN) else ''))
            panels.append((ref_label, ref_crop))

            canvas = compose_grid(panels, title_fmt.format(label=name),
                                  font_path=font_path)
            target = out_dir / f'{prefix}{name}.png'
            canvas.save(target)
            out = target                             # save 成功才置非 None（回调契约）
            outs.append(out)
            if verbose:
                print(f'[{i}/{total}] {out.name}  [{", ".join(notes)}]', flush=True)
        except Exception as exc:                          # 单点失败不拖垮整批
            if verbose:
                print(f'[{i}/{total}] {name} 失败: {exc}', flush=True)
        if on_progress is not None:
            on_progress(i, total, name, out)
    return outs


# ---------------------------------------------------------------- CLI

def _parse_spot(spec: str):
    """'自行车=0.783,0.820' → ('自行车', 0.783, 0.820)"""
    name, _, xy = spec.partition('=')
    try:
        fx, fy = (float(v) for v in xy.split(','))
    except ValueError:
        raise SystemExit(f'--spot 格式应为 名=x,y（0~1 相对坐标）: {spec!r}')
    if not (0 <= fx <= 1 and 0 <= fy <= 1):
        raise SystemExit(f'裁切点坐标要在 0~1 之间: {spec!r}')
    return name.strip() or f'位置{fx:.2f}_{fy:.2f}', fx, fy


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description='四格对比图：多帧合成/单帧的同一裁切、同一色调并排比较',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ref', required=True, help='参考图（全帧数合成成品 JPG/TIF）')
    ap.add_argument('--panel', action='append', default=[], metavar='标签=路径',
                    help='对比面板，可重复，如 --panel "8 帧合成=top8_2x.jpg"')
    ap.add_argument('--spot', action='append', default=[], metavar='名=x,y',
                    help='裁切点（相对坐标 0~1），可重复，如 --spot 自行车=0.783,0.820')
    ap.add_argument('--out', required=True, help='输出目录（自动创建）')
    ap.add_argument('--crop', default=f'{DEFAULT_CROP[0]}x{DEFAULT_CROP[1]}',
                    help=f'裁切尺寸 宽x高（默认 {DEFAULT_CROP[0]}x{DEFAULT_CROP[1]}）')
    ap.add_argument('--title', default=DEFAULT_TITLE,
                    help='标题模板，{label} 会替换成点名')
    ap.add_argument('--prefix', default=DEFAULT_PREFIX, help='输出文件名前缀')
    ap.add_argument('--font', default=None,
                    help='字体路径（默认微软雅黑；找不到退 Pillow 默认，中文会变方块）')
    ap.add_argument('--quiet', action='store_true', help='不逐点打印')
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    panels = []
    for spec in args.panel:
        label, _, path = spec.partition('=')
        if not Path(path.strip()).is_file():
            print(f'面板图不存在: {path.strip()}')
            return 2
        panels.append((label.strip() or path.strip(), Path(path.strip())))
    spots = [_parse_spot(s) for s in args.spot]
    if not spots:
        print('至少给一个 --spot')
        return 2
    try:
        cw, ch = (int(v) for v in args.crop.lower().split('x'))
    except ValueError:
        print(f'--crop 格式应为 宽x高: {args.crop!r}')
        return 2

    outs = generate_comparisons(Path(args.ref), panels, spots, Path(args.out),
                                crop_wh=(cw, ch), title_fmt=args.title,
                                font_path=args.font, prefix=args.prefix,
                                verbose=not args.quiet)
    print(f'\n生成 {len(outs)}/{len(spots)} 张 → {args.out}')
    return 0 if outs else 1


if __name__ == '__main__':
    sys.exit(main())
