# -*- coding: utf-8 -*-
"""自动找连拍小位移组 → 跑超分 → 出宣传对比图

三步（扫描结果缓存，避免每次重扫 5 分钟）：
    python devtests/sr_promo.py scan                 # 扫描 RAW 根目录，写缓存
    python devtests/sr_promo.py contact              # 候选缩略图九宫格，人工挑
    python devtests/sr_promo.py promo --pick 0 1 2   # 处理并出宣传图
"""
import argparse
import os
import json
import sys
import time
from pathlib import Path

import numpy as np
import cv2

sys.path.insert(0, r'D:\Software files\photo_watermark')
import sr_suggest
import sr_process

RAW_ROOT = Path(r'C:\Users\LQL\Pictures\raw')
OUT_DIR = RAW_ROOT / 'SR输出' / '宣传图'
CACHE = Path(r'D:\Software files\photo_watermark\devtests\_sr_promo_scan.json')
SKIP_DIRS = {'SR输出', '防抖测试结果', '防抖测试结果_200-245', '_lut', 'JSR_Output',
             'watermark_output', '.stversions'}


def candidate_dirs(root):
    out = []
    for d in sorted(root.rglob('*')):
        if not d.is_dir():
            continue
        if d.name in SKIP_DIRS or any(p in SKIP_DIRS for p in d.parts):
            continue
        n = sum(1 for p in d.iterdir()
                if p.is_file() and p.suffix.lower() in sr_suggest.SCAN_EXTS)
        if n >= 4:
            out.append((d, n))
    return out


def cmd_scan(root=RAW_ROOT):
    t0 = time.time()
    cands, ok = [], []
    for d, n in candidate_dirs(root):
        print(f'扫描 {d.name}（{n} 张）', flush=True)
        try:
            groups, _singles = sr_suggest.scan_photos(d, gap=2.0, min_frames=4)
        except Exception as e:
            print(f'  [跳过] {e}')
            continue
        for ts, files in groups:
            try:
                v = sr_suggest.judge_group(files, start_time=ts)
            except Exception as e:
                print(f'  [判定失败] {e}')
                continue
            cands.append((str(d), v))
    # 只存可超分的，按"画面变化小"优先
    def key(e):
        d, v = e
        m = v.metrics or {}
        spread = m.get('subpixel_spread_px', 0) or 0
        shift = m.get('max_shift_px', 999) or 999
        return (min(len(v.files), 16) / 16.0) * 0.5 + spread * 0.3 + \
               max(0.0, 1.0 - shift / 60.0) * 0.2
    ok = [(d, v) for d, v in cands if v.status == 'ok']
    ok.sort(key=key, reverse=True)
    data = [dict(dir=d, start=v.start_time, files=[str(f) for f in v.files],
                 reason=v.reason, metrics=v.metrics) for d, v in ok]
    CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n可超分组 {len(ok)} 个，已写缓存 {CACHE}（{time.time()-t0:.0f}s）')


def load_cache():
    return json.loads(CACHE.read_text(encoding='utf-8'))


def _label(img, text, org=(10, 28), size=22):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(im)
    font = None
    for fp in (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf'):
        if Path(fp).exists():
            font = ImageFont.truetype(fp, size)
            break
    d.text(org, text, font=font, fill=(255, 255, 255), stroke_width=2,
           stroke_fill=(0, 0, 0))
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


def cmd_contact(n=12, cols=4, cell=480):
    data = load_cache()[:n]
    tiles = []
    for i, g in enumerate(data):
        f = Path(g['files'][len(g['files']) // 2])
        try:
            img = sr_process.read_raw_camera_linear(f, half_size=True)
            a = np.clip(img, 0, 1)
            a = np.power(a, 1 / 2.2)
            u8 = cv2.cvtColor((a * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
        except Exception as e:
            u8 = np.full((cell, cell, 3), 40, np.uint8)
            print('读图失败', f, e)
        h, w = u8.shape[:2]
        s = cell / max(h, w)
        u8 = cv2.resize(u8, (max(1, int(w * s)), max(1, int(h * s))),
                        interpolation=cv2.INTER_AREA)
        canvas = np.full((cell, cell, 3), 24, np.uint8)
        y0 = (cell - u8.shape[0]) // 2
        x0 = (cell - u8.shape[1]) // 2
        canvas[y0:y0 + u8.shape[0], x0:x0 + u8.shape[1]] = u8
        ts = time.strftime('%m-%d %H:%M', time.localtime(g['start']))
        m = g['metrics'] or {}
        t = _label(canvas, f"#{i} {Path(g['dir']).name} {ts}", (8, 6), 20)
        t = _label(t, f"{len(g['files'])}帧 位移{m.get('max_shift_px',0):.0f}px "
                      f"亚像素{m.get('subpixel_spread_px',0):.2f}", (8, 34), 18)
        tiles.append(t)
    rows = []
    for r in range(0, len(tiles), cols):
        row = tiles[r:r + cols]
        while len(row) < cols:
            row.append(np.full((cell, cell, 3), 24, np.uint8))
        rows.append(np.concatenate(row, axis=1))
    sheet = np.concatenate(rows, axis=0)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = OUT_DIR / '候选缩略图.jpg'
    ok, buf = cv2.imencode('.jpg', sheet, [cv2.IMWRITE_JPEG_QUALITY, 90])
    p.write_bytes(buf.tobytes())
    print('wrote', p, sheet.shape)


def _exif_line(path):
    """读一张的代表性 EXIF，拼一行宣传用参数"""
    try:
        import exifread
        with open(str(path), 'rb') as f:
            t = exifread.process_file(f, details=False)
        cam = str(t.get('Image Model', '')).strip()
        lens = str(t.get('EXIF LensModel', '')).strip()
        foc = str(t.get('EXIF FocalLength', '')).strip()
        fnum = str(t.get('EXIF FNumber', '')).strip()
        exp = str(t.get('EXIF ExposureTime', '')).strip()
        iso = str(t.get('EXIF ISOSpeedRatings', '')).strip()
        bits = [x for x in (cam, lens, (foc + 'mm') if foc else '',
                            ('f/' + fnum) if fnum else '',
                            (exp + 's') if exp else '', ('ISO ' + iso) if iso else '') if x]
        return ' · '.join(bits), cam
    except Exception:
        return '', ''


def _exif_orientation(path):
    try:
        import exifread
        with open(str(path), 'rb') as f:
            t = exifread.process_file(f, details=False)
        o = t.get('Image Orientation')
        return int(o.values[0]) if o else 1
    except Exception:
        return 1


def _orient(img, o):
    if o == 3:
        return cv2.rotate(img, cv2.ROTATE_180)
    if o == 6:
        return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    if o == 8:
        return cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return img


def _pil_font(size):
    from PIL import ImageFont
    for fp in (r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf'):
        if Path(fp).exists():
            return ImageFont.truetype(fp, size)
    return None


def _text(draw, xy, s, size=28, fill=(30, 30, 30), bold=False):
    draw.text(xy, s, font=_pil_font(size), fill=fill,
              stroke_width=0)


def build_promo(pick_idx, scale=2, layout_only=False):
    """处理指定组并出宣传图（每组一张 PNG）"""
    from PIL import Image, ImageDraw
    data = load_cache()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    made = []
    for i in pick_idx:
        g = data[i]
        files = [Path(f) for f in g['files']]
        tag = f"组{i:02d}_{Path(g['dir']).name}_{time.strftime('%m%d%H%M', time.localtime(g['start']))}"
        base = OUT_DIR / tag
        jpg = Path(str(base) + f'_{scale}x.jpg')
        if not jpg.exists() and not layout_only:
            print(f'[{i}] 处理 {len(files)} 帧 -> {base}', flush=True)
            sr_process.process(files=[str(f) for f in files],
                               output_path=str(base) + '.tif', scale=scale,
                               max_frames=16, threads=8)
        else:
            print(f'[{i}] 已有 {jpg.name}，跳过合成', flush=True)

        # 合成结果（产品 JPG）
        sr_u8 = cv2.imdecode(np.fromfile(str(jpg), dtype=np.uint8),
                             cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
        # 单帧（最锐）走同一色调映射
        g_half = [sr_process.read_raw_as_gray(f, half_size=True) for f in files]
        sharp = [float((x - cv2.GaussianBlur(x, (0, 0), 2.0)).std()) for x in g_half]
        best = files[int(np.argmax(sharp))]
        lin = sr_process.read_raw_camera_linear(best)
        A, b = sr_process.derive_camera_to_srgb(best) or (np.eye(3), np.zeros(3))
        lin2 = cv2.resize(np.clip(lin, 0, 1), None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_CUBIC)
        one_u8 = sr_process.render_srgb_preview(lin2.astype(np.float32), (A, b),
                                                stretch=True)
        del lin, lin2
        one_u8 = cv2.cvtColor(one_u8, cv2.COLOR_RGB2BGR)
        # 管线输出的像素已经是正立方向（rawpy 解码时就转过了），
        # 只是 EXIF Orientation 标签没改；这里按像素用，不再旋转

        made.append((i, g, tag, jpg, sr_u8, one_u8, best))
    return made


def _align_offset(ref_gray, mov_gray):
    """mov（单帧全幅）相对 ref（合成结果，是原图的裁剪）的偏移

    合成结果是被裁过的子图，直接 phaseCorrelate 会被裁切边界带偏（实测
    偏移 1785px 完全错位）。改用取子图中心块做模板匹配定粗位，
    再用半尺寸相位相关精修到亚像素。
    """
    def half(g, k):
        return cv2.resize(g, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    a4, b4 = half(ref_gray, 0.25), half(mov_gray, 0.25)
    ph = min(400, a4.shape[0] // 2, b4.shape[0])
    pw = min(400, a4.shape[1] // 2, b4.shape[1])
    ay, ax = a4.shape[0] // 2 - ph // 2, a4.shape[1] // 2 - pw // 2
    patch = a4[ay:ay + ph, ax:ax + pw]
    res = cv2.matchTemplate(b4, patch, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(res)
    dx = int(round((loc[0] - ax) / 0.25))
    dy = int(round((loc[1] - ay) / 0.25))
    # 半尺寸相位相关精修
    a2, b2 = half(ref_gray, 0.5), half(mov_gray, 0.5)
    py, px = a2.shape[0] // 2, a2.shape[1] // 2
    r = 300
    ay0, ax0 = max(0, py - r), max(0, px - r)
    ref_p = a2[ay0:min(a2.shape[0], py + r), ax0:min(a2.shape[1], px + r)]
    my, mx0 = py + dy // 2, px + dx // 2
    by0, bx0 = max(0, my - r), max(0, mx0 - r)
    mov_p = b2[by0:min(b2.shape[0], my + r), bx0:min(b2.shape[1], mx0 + r)]
    hh = min(ref_p.shape[0], mov_p.shape[0]); ww = min(ref_p.shape[1], mov_p.shape[1])
    if hh > 64 and ww > 64:
        win = cv2.createHanningWindow((ww, hh), cv2.CV_32F)
        (fx, fy), _ = cv2.phaseCorrelate(np.float32(ref_p[:hh, :ww]) * win,
                                         np.float32(mov_p[:hh, :ww]) * win)
        dx += int(round(fx * 2)); dy += int(round(fy * 2))
    print(f'  模板匹配 {mx:.3f}')
    return dx, dy


def _align_matrix(sr_gray, one_gray):
    """估计 单帧 -> 合成结果 的仿射（平移+旋转），返回 2x3（合成坐标 -> 单帧坐标）

    合成画布可能带微小旋转（手抖/透视精修），纯平移在远离中心的地方会错位；
    先模板匹配定粗位，再 ECC 欧氏精修。
    """
    def q(g, k=4):
        return cv2.resize(g, None, fx=1.0 / k, fy=1.0 / k,
                          interpolation=cv2.INTER_AREA).astype(np.float32)
    a, b = q(sr_gray), q(one_gray)
    ph = min(300, a.shape[0] // 2); pw = min(300, a.shape[1] // 2)
    ay, ax = a.shape[0] // 2 - ph // 2, a.shape[1] // 2 - pw // 2
    res = cv2.matchTemplate(b, a[ay:ay + ph, ax:ax + pw], cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    W = np.array([[1.0, 0.0, float(loc[0] - ax)],
                  [0.0, 1.0, float(loc[1] - ay)]], np.float32)
    try:
        cc, W = cv2.findTransformECC(a, b, W, cv2.MOTION_EUCLIDEAN,
                                     (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5),
                                     None, 5)
    except cv2.error:
        pass
    M = W.astype(np.float64).copy()
    M[:, 2] *= 4.0
    ang = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
    print(f'  对齐: 匹配 {score:.3f} 旋转 {ang:+.2f}° 平移 ({M[0,2]:+.0f},{M[1,2]:+.0f})')
    return M


def _detail_window(img, w, h, margin=0.06):
    """选一块细节多、中间调、没削顶的窗口（宣传图要能看出差别）"""
    g = img.mean(axis=2).astype(np.float32) / 255.0
    H, W = g.shape
    if H < h + 2 * int(H * margin) or W < w + 2 * int(W * margin):
        return (W - w) // 2, (H - h) // 2
    lap = np.abs(cv2.Laplacian(g, cv2.CV_32F, ksize=3))
    clipped = ((g >= 0.97) | (g <= 0.02)).astype(np.float32)
    I_lap = cv2.integral(lap)
    I_clip = cv2.integral(clipped)
    I_g = cv2.integral(g)
    step = max(12, w // 10)
    my, mx = int(H * margin), int(W * margin)
    best, bx, by = -1e9, mx, my
    for y in range(my, H - h - my, step):
        for x in range(mx, W - w - mx, step):
            area = float(w * h)
            s_lap = I_lap[y + h, x + w] - I_lap[y, x + w] - I_lap[y + h, x] + I_lap[y, x]
            s_clip = I_clip[y + h, x + w] - I_clip[y, x + w] - I_clip[y + h, x] + I_clip[y, x]
            s_g = I_g[y + h, x + w] - I_g[y, x + w] - I_g[y + h, x] + I_g[y, x]
            mean = s_g / area
            clip_frac = s_clip / area
            if not (0.10 <= mean <= 0.85):
                continue
            score = (s_lap / area) * (1.0 - clip_frac) ** 2
            if score > best:
                best, bx, by = score, x, y
    return bx, by


def layout_one(i, g, tag, sr_u8, one_u8):
    """宣传图：左=全画面（带裁切框），右=100% 与 300% 两组对照"""
    from PIL import Image, ImageDraw
    H, W = sr_u8.shape[:2]
    og = cv2.cvtColor(one_u8, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    sg = cv2.cvtColor(sr_u8, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    M = _align_matrix(sg, og)
    one_warp = cv2.warpAffine(one_u8, M.astype(np.float32), (W, H),
                              flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP,
                              borderMode=cv2.BORDER_REPLICATE)

    cw, ch = 620, 440
    x, y = _detail_window(sr_u8, cw, ch)
    crop_sr = sr_u8[y:y + ch, x:x + cw]
    crop_one = one_warp[y:y + ch, x:x + cw]

    zw, zh = cw // 3, ch // 3
    zx, zy = x + (cw - zw) // 2, y + (ch - zh) // 2
    z_sr = cv2.resize(sr_u8[zy:zy + zh, zx:zx + zw], (cw, ch), interpolation=cv2.INTER_NEAREST)
    z_one = cv2.resize(one_warp[zy:zy + zh, zx:zx + zw], (cw, ch),
                       interpolation=cv2.INTER_NEAREST)

    PAD = 60
    GC = 26                      # 左右两栏间距
    right_w = cw * 2 + 16
    canvas_w = 3000
    left_w = canvas_w - PAD * 2 - GC - right_w
    head_h = 250
    row_gap = 54
    canvas_h = head_h + (ch + 40) * 2 + row_gap + 60
    canvas = Image.new('RGB', (canvas_w, canvas_h), (246, 247, 249))
    dr = ImageDraw.Draw(canvas)

    _text(dr, (PAD, 44), '多帧超分 · 手持连拍合成', 56, (24, 27, 33))
    _text(dr, (PAD, 118), f'{len(g["files"])} 帧连拍 → 2x 输出 · {W}×{H} px',
          30, (88, 94, 104))
    exif, _cam = _exif_line(Path(g['files'][0]))
    _text(dr, (PAD, 160), exif, 23, (120, 126, 136))
    _text(dr, (PAD, 196), '左：合成结果全画面 · 右：同一景物的单帧 / 合成像素对照',
          22, (150, 156, 166))

    # 左：全画面
    top, bottom = head_h, canvas_h - 50
    fit = min(left_w / W, (bottom - top) / H)
    fw, fh = int(W * fit), int(H * fit)
    fx = PAD + (left_w - fw) // 2
    fy = top + (bottom - top - fh) // 2
    full = cv2.resize(sr_u8, (fw, fh), interpolation=cv2.INTER_AREA)
    canvas.paste(Image.fromarray(cv2.cvtColor(full, cv2.COLOR_BGR2RGB)), (fx, fy))
    rx0, ry0 = fx + int(x * fit), fy + int(y * fit)
    rx1, ry1 = rx0 + int(cw * fit), ry0 + int(ch * fit)
    dr.rectangle([rx0, ry0, rx1, ry1], outline=(255, 70, 60), width=4)
    dr.rectangle([rx0, max(fy, ry0 - 30), rx0 + 140, max(fy + 4, ry0 - 4)],
                 fill=(255, 70, 60))
    _text(dr, (rx0 + 8, max(fy + 2, ry0 - 28)), '对比区域', 19, (255, 255, 255))

    # 右：两组对照
    rx = PAD + left_w + GC
    for k, (a_img, b_img, title) in enumerate((
            (crop_one, crop_sr, '100% 像素对照'),
            (z_one, z_sr, '300% 放大对照'))):
        ry = head_h + k * (ch + 40 + row_gap)
        _text(dr, (rx, ry - 36), title, 25, (60, 64, 72))
        for j, (im, lab, col) in enumerate(((a_img, '单帧（最锐）', (140, 146, 156)),
                                            (b_img, '多帧合成', (255, 70, 60)))):
            px = rx + j * (cw + 16)
            canvas.paste(Image.fromarray(cv2.cvtColor(im, cv2.COLOR_BGR2RGB)), (px, ry))
            dr.rectangle([px, ry, px + cw, ry + ch], outline=col, width=3)
            dr.rectangle([px, ry + ch - 34, px + 170, ry + ch], fill=col)
            _text(dr, (px + 10, ry + ch - 31), lab, 20, (255, 255, 255))

    outp = OUT_DIR / f'宣传图_{tag}.png'
    # 原子写：先写临时名再替换，避免目标已存在时 PIL 偶发 EINVAL；
    # 也不要为了重写去删用户看得见的成品
    tmp = OUT_DIR / f'.{tag}.tmp.png'
    canvas.save(tmp)
    os.replace(tmp, outp)
    print('  wrote', outp)


def cmd_promo(pick, layout_only=False):
    made = build_promo(pick, layout_only=layout_only)
    for i, g, tag, jpg, sr_u8, one_u8, best in made:
        print(f'[{i}] {tag}: 合成 {sr_u8.shape[1]}x{sr_u8.shape[0]}')
        layout_one(i, g, tag, sr_u8, one_u8)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('scan')
    c = sub.add_parser('contact')
    c.add_argument('-n', type=int, default=12)
    pr = sub.add_parser('promo')
    pr.add_argument('--pick', type=int, nargs='+', required=True)
    pr.add_argument('--layout-only', action='store_true')
    a = ap.parse_args()
    if a.cmd == 'scan':
        cmd_scan()
    elif a.cmd == 'contact':
        cmd_contact(a.n)
    else:
        cmd_promo(a.pick, layout_only=getattr(a, "layout_only", False))


if __name__ == '__main__':
    main()
