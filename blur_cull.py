#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""糊片清理：连拍分组 → 组内相对锐度 → 明显糊的帧移入回收站

判据
----
逐帧解码成灰度（RAW 用 2×2 合并的半尺寸，JPG 用 2×2 缩减），算 3×3 拉普拉斯
方差作为锐度；再除以同组最高分，得到"相对锐度"。

    相对锐度 ≥ --review (0.90)      保留
    --ratio ≤ 相对 < --review       软：只标记，不删
    相对锐度 < --ratio (0.80)       糊：删除

为什么要先 2×2 合并再算拉普拉斯：夜景高感时，全分辨率拉普拉斯方差会被噪点
抬高。实测同一场景 1/10s 与 1/20s 两批，全分辨率差 80%、4× 降采样后只差 3%，
直接在全分辨率上比会把"噪点最多的那张"误判成最锐。2×2 合并把噪声压 2 倍，
而 2–3 像素的拖影还在，正好是手持糊片的特征尺度。

用法
----
    py blur_cull.py "C:\\Pictures\\raw\\109_PANA"                     # 只出报告
    py blur_cull.py "C:\\Pictures\\raw\\109_PANA" --after P1093278.RW2 # 只处理这之后
    py blur_cull.py "C:\\Pictures\\raw\\109_PANA" --apply              # 真删（回收站）

默认 dry-run，不动任何文件；报告与联系表写到 %TEMP%\\blur_cull\\<文件夹名>\\。
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import cv2
import numpy as np

RAW_EXTS = ('.nef', '.nrw', '.arw', '.cr2', '.cr3', '.orf', '.raf', '.dng',
            '.rw2', '.pef', '.srw', '.x3f', '.raw')
SIDECAR_EXTS = ('.jpg', '.jpeg')

DEC_KEEP, DEC_SOFT, DEC_BLUR = 'keep', 'soft', 'blur'
DEC_LABEL = {DEC_KEEP: '保留', DEC_SOFT: '软', DEC_BLUR: '糊'}
DEC_COLOR = {DEC_KEEP: (90, 90, 90), DEC_SOFT: (0, 165, 255), DEC_BLUR: (0, 0, 255)}

DEFAULT_RATIO = 0.80
DEFAULT_REVIEW = 0.90
DEFAULT_GAP = 30.0


# ---------------------------------------------------------------- 数据结构

@dataclass
class Frame:
    """一帧的路径、EXIF、锐度与判定。"""
    path: Path
    ts: float | None = None
    shutter: str = '-'
    iso: str = '-'
    fnum: str = '-'
    focal: str = '-'
    score: float = 0.0
    rel: float = 1.0
    group: int = 0
    decision: str = DEC_KEEP
    scored: bool = False        # 解码失败/算不出锐度的帧不参与判定，也绝不删

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def time_text(self) -> str:
        if self.ts is None:
            return '-'
        return _dt.datetime.fromtimestamp(self.ts).strftime('%Y-%m-%d %H:%M:%S')


# ---------------------------------------------------------------- 扫描与筛选

def frame_index(path: Path) -> int | None:
    """文件名末尾的编号，用于 --after / --first / --last 排序。"""
    m = re.search(r'(\d+)$', path.stem)
    return int(m.group(1)) if m else None


def scan_folder(folder: Path) -> list[Path]:
    """列出文件夹里的 RAW（不递归）。"""
    return sorted(p for p in Path(folder).iterdir()
                  if p.is_file() and p.suffix.lower() in RAW_EXTS)


def _as_index(value) -> int | None:
    """把 'P1093278.RW2' / 'P1093278' / 1093278 统一成编号。"""
    if isinstance(value, int):
        return value
    if value is None:
        return None
    return frame_index(Path(str(value)))


def filter_files(paths: Sequence[Path], after=None, first=None, last=None) -> list[Path]:
    """按编号范围筛选：after 之后（不含）、first..last 闭区间。无编号文件只在无筛选时保留。"""
    lo, hi = _as_index(first), _as_index(last)
    after_idx = _as_index(after)
    if lo is None and hi is None and after_idx is None:
        return list(paths)
    picked = []
    for p in paths:
        idx = frame_index(p)
        if idx is None:
            continue
        if after_idx is not None and idx <= after_idx:
            continue
        if lo is not None and idx < lo:
            continue
        if hi is not None and idx > hi:
            continue
        picked.append(p)
    return picked


# ---------------------------------------------------------------- EXIF 与解码

def read_meta(path: Path, exifread_mod=None) -> dict:
    """读拍摄时间与曝光参数；任何一项读不到都退回 '-'。"""
    if exifread_mod is None:
        import exifread as exifread_mod
    tags = {}
    try:
        with open(str(path), 'rb') as f:
            tags = exifread_mod.process_file(f, details=False)
    except Exception:
        return {'ts': None}

    def get(*names):
        for n in names:
            if n in tags:
                return str(tags[n]).strip()
        return '-'

    ts = None
    raw_dt = get('EXIF DateTimeOriginal', 'EXIF DateTimeDigitized', 'Image DateTime')
    m = re.match(r'(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})', raw_dt)
    if m:
        ts = _dt.datetime(*map(int, m.groups())).timestamp()
        sub = get('EXIF SubSecTimeOriginal', 'EXIF SubSecTime', 'EXIF SubSecTimeDigitized')
        if sub.isdigit():
            ts += int(sub[-3:].ljust(3, '0')) / 1000.0
    return {
        'ts': ts,
        'shutter': get('EXIF ExposureTime'),
        'iso': get('EXIF ISOSpeedRatings', 'EXIF PhotographicSensitivity'),
        'fnum': get('EXIF FNumber'),
        'focal': get('EXIF FocalLength'),
    }


def _imread_unicode(path: Path, flags=cv2.IMREAD_COLOR) -> np.ndarray:
    """OpenCV 在 Windows 下读不了中文路径，走字节流。"""
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, flags)
    if img is None:
        raise IOError(f'无法解码: {path}')
    return img


def _imwrite_unicode(path: Path, img: np.ndarray, quality: int = 92):
    ext = path.suffix or '.png'
    params = [cv2.IMWRITE_JPEG_QUALITY, quality] if ext.lower() in ('.jpg', '.jpeg') else []
    ok, buf = cv2.imencode(ext, img, params)
    if not ok:
        raise IOError(f'编码失败: {path}')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    buf.tofile(str(path))


def decode_gray(path: Path) -> np.ndarray:
    """解码为 float64 灰度，RAW 走 2×2 合并、JPG 走 2×2 缩减（两者尺度一致）。"""
    if path.suffix.lower() in RAW_EXTS:
        import rawpy
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(half_size=True, use_camera_wb=True,
                                  no_auto_bright=True, output_bps=8,
                                  gamma=(2.222, 4.5))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float64)
    g = _imread_unicode(path, cv2.IMREAD_REDUCED_GRAYSCALE_2)
    return g.astype(np.float64)


def sharpness(gray: np.ndarray) -> float:
    """3×3 拉普拉斯方差：越糊越小。"""
    return float(cv2.Laplacian(gray, cv2.CV_64F, ksize=3).var())


# ---------------------------------------------------------------- 分组与判定

def group_by_time(frames: Sequence[Frame], gap: float = DEFAULT_GAP) -> list[list[Frame]]:
    """按拍摄时间分组：相邻两帧间隔 ≤ gap 秒算同一组。无时间的帧各自成组。"""
    ordered = sorted(frames, key=lambda f: (f.ts is None, f.ts if f.ts is not None else 0.0,
                                            str(f.path.name)))
    groups: list[list[Frame]] = []
    for f in ordered:
        prev = groups[-1][-1] if groups else None
        if (prev is not None and f.ts is not None and prev.ts is not None
                and f.ts - prev.ts <= gap):
            groups[-1].append(f)
        else:
            groups.append([f])
    return groups


def score_frames(frames: Iterable[Frame], exifread_mod=None, verbose: bool = True) -> None:
    """逐帧解码打分，结果写回 frame.score。"""
    frames = list(frames)
    total = len(frames)
    for i, f in enumerate(frames, 1):
        meta = read_meta(f.path, exifread_mod)
        f.ts, f.shutter, f.iso = meta.get('ts'), meta.get('shutter', '-'), meta.get('iso', '-')
        f.fnum, f.focal = meta.get('fnum', '-'), meta.get('focal', '-')
        try:
            f.score = sharpness(decode_gray(f.path))
            f.scored = True
        except Exception as exc:                      # 解码失败不中断整批
            f.score = 0.0
            f.scored = False
            if verbose:
                print(f'  [跳过] 解码失败 {f.name}: {exc}', flush=True)
        if verbose:
            print(f'  [{i:>3}/{total}] {f.name}  锐度 {f.score:8.0f}', flush=True)


def assign_relative(groups: Sequence[Sequence[Frame]]) -> None:
    """组内相对锐度：本组最高分为 1.0（算不出锐度的帧不参与）。"""
    for gi, g in enumerate(groups, 1):
        best = max((f.score for f in g if f.scored), default=0.0)
        for f in g:
            f.group = gi
            f.rel = (f.score / best) if (best > 0 and f.scored) else 0.0


def decide(groups: Sequence[Sequence[Frame]], ratio: float = DEFAULT_RATIO,
           review: float = DEFAULT_REVIEW) -> None:
    """按相对锐度落判定。单帧组没有同场景对照、算不出锐度的帧都不判糊。"""
    for g in groups:
        for f in g:
            if not f.scored or len(g) < 2:
                f.decision = DEC_KEEP
            elif f.rel < ratio:
                f.decision = DEC_BLUR
            elif f.rel < review:
                f.decision = DEC_SOFT
            else:
                f.decision = DEC_KEEP


def exposure_conflicts(group: Sequence[Frame]) -> list[str]:
    """组内快门/ISO 不一致时返回说明，提示噪声混淆（快门快的未必真的更锐）。"""
    notes = []
    shutters = {f.shutter for f in group if f.shutter not in ('-', '')}
    isos = {f.iso for f in group if f.iso not in ('-', '')}
    if len(shutters) > 1:
        notes.append('快门 ' + '/'.join(sorted(shutters)))
    if len(isos) > 1:
        notes.append('ISO ' + '/'.join(sorted(isos)))
    return notes


# ---------------------------------------------------------------- 报告输出

def write_csv(frames: Sequence[Frame], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['文件', '组', '拍摄时间', '快门', 'ISO', '光圈', '焦距',
                    '锐度', '相对锐度%', '判定'])
        for fr in frames:
            w.writerow([fr.name, fr.group, fr.time_text, fr.shutter, fr.iso, fr.fnum,
                        fr.focal, f'{fr.score:.0f}', f'{fr.rel * 100:.1f}',
                        DEC_LABEL[fr.decision]])
    return path


def _thumb(path: Path, tile: int) -> np.ndarray:
    """缩略图：优先用同名 JPG（快），否则解 RAW 半尺寸。"""
    side = sidecar_for(path)
    if side is not None:
        img = _imread_unicode(side, cv2.IMREAD_REDUCED_COLOR_4)
    else:
        img = decode_gray(path)
        img = cv2.cvtColor(img.astype(np.uint8), cv2.COLOR_GRAY2BGR)
    h, w = img.shape[:2]
    nh = max(1, int(h * tile / w))
    return cv2.resize(img, (tile, nh), interpolation=cv2.INTER_AREA)


def write_contact_sheet(frames: Sequence[Frame], path: Path, cols: int = 6,
                        tile: int = 320) -> Path:
    """联系表：糊的描红框、软的描橙框，角上写文件名与相对锐度。"""
    path = Path(path)
    tiles = []
    for fr in frames:
        try:
            t = _thumb(fr.path, tile)
        except Exception:
            t = np.zeros((tile // 2, tile, 3), dtype=np.uint8)
        t = cv2.copyMakeBorder(t, 3, 3, 3, 3, cv2.BORDER_CONSTANT,
                               value=DEC_COLOR[fr.decision])
        cv2.rectangle(t, (6, 6), (tile - 2, 30), (0, 0, 0), -1)
        cv2.putText(t, f'{fr.name} {fr.rel * 100:.0f}%', (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(t)
    if not tiles:
        return path
    hh = max(t.shape[0] for t in tiles)
    ww = max(t.shape[1] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, hh - t.shape[0], 0, ww - t.shape[1],
                                cv2.BORDER_CONSTANT, value=(20, 20, 20)) for t in tiles]
    rows = []
    for i in range(0, len(tiles), cols):
        row = tiles[i:i + cols]
        while len(row) < cols:
            row.append(np.full_like(tiles[0], 20))
        rows.append(np.hstack(row))
    _imwrite_unicode(path, np.vstack(rows))
    return path


# ---------------------------------------------------------------- 删除

def sidecar_for(raw: Path) -> Path | None:
    """同名的 JPG 侧车（返回磁盘上的真实文件名，不是拼出来的大小写）。"""
    try:
        for p in Path(raw).parent.iterdir():
            if p.is_file() and p.stem == raw.stem and p.suffix.lower() in SIDECAR_EXTS:
                return p
    except OSError:
        return None
    return None


def _shell_recycle(paths: Sequence[Path]) -> int:
    """调 SHFileOperationW 移入回收站，返回 API 返回码（0 = 成功）。"""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [('hwnd', wintypes.HWND), ('wFunc', wintypes.UINT),
                    ('pFrom', wintypes.LPCWSTR), ('pTo', wintypes.LPCWSTR),
                    ('fFlags', ctypes.c_uint16), ('fAnyOperationsAborted', wintypes.BOOL),
                    ('hNameMappings', ctypes.c_void_p), ('lpszProgressTitle', wintypes.LPCWSTR)]

    FO_DELETE = 3
    FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 0x0004, 0x0010, 0x0040, 0x0400
    flags = FOF_SILENT | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_ALLOWUNDO
    p_from = ''.join(str(p) + '\0' for p in paths) + '\0'
    op = SHFILEOPSTRUCTW(None, FO_DELETE, p_from, None, flags, False, None, None)
    return int(ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)))


def delete_files(paths: Sequence[Path], recycle: bool = True) -> tuple[list[Path], list[Path]]:
    """删除文件，返回 (成功, 失败)。recycle=True 走回收站（可恢复）。"""
    paths = [Path(p) for p in paths if Path(p).exists()]
    if not paths:
        return [], []
    if not recycle:
        done, failed = [], []
        for p in paths:
            try:
                os.remove(p)
                done.append(p)
            except OSError:
                failed.append(p)
        return done, failed

    if len(paths) == 1:
        _shell_recycle(paths)
    else:
        code = _shell_recycle(paths)
        if code != 0:                                  # 批量失败时退回逐张
            for p in paths:
                _shell_recycle([p])
    done = [p for p in paths if not p.exists()]
    failed = [p for p in paths if p.exists()]
    return done, failed


def collect_deletions(frames: Sequence[Frame], with_sidecar: bool = True) -> list[Path]:
    """待删文件：糊片 +（可选）同名 JPG 侧车。"""
    out: list[Path] = []
    for f in frames:
        out.append(f.path)
        if with_sidecar:
            side = sidecar_for(f.path)
            if side is not None:
                out.append(side)
    return out


# ---------------------------------------------------------------- 主流程

def default_out_dir(folder: Path) -> Path:
    return Path(tempfile.gettempdir()) / 'blur_cull' / Path(folder).name


def build_frames(folder: Path, after=None, first=None, last=None,
                 verbose: bool = True) -> list[Frame]:
    raws = filter_files(scan_folder(folder), after=after, first=first, last=last)
    if verbose:
        print(f'扫描 {folder}：{len(raws)} 张 RAW')
    if not raws:
        return []
    frames = [Frame(path=p) for p in raws]
    score_frames(frames, verbose=verbose)
    return frames


def report(frames: Sequence[Frame], out_dir: Path, apply: bool, recycle: bool,
           with_sidecar: bool, gap: float, ratio: float,
           review: float = DEFAULT_REVIEW, confirm: bool = False) -> int:
    groups = group_by_time(frames, gap=gap)
    assign_relative(groups)
    decide(groups, ratio=ratio, review=review)

    print(f'\n分组：{len(groups)} 组（组内间隔 ≤ {gap:g}s）')
    for gi, g in enumerate(groups, 1):
        best = max(g, key=lambda f: f.score)
        t0 = g[0].time_text
        print(f'  组{gi}: {g[0].name} ~ {g[-1].name}  {len(g)} 帧  {t0}  '
              f'最佳 {best.name} 锐度 {best.score:.0f}')
        notes = exposure_conflicts(g)
        if notes:
            print(f'        [提示] 组内曝光不一致（{"，".join(notes)}），'
                  f'噪声会抬高锐度，结论请结合联系表看')

    blur = [f for f in frames if f.decision == DEC_BLUR]
    soft = [f for f in frames if f.decision == DEC_SOFT]
    keep = [f for f in frames if f.decision == DEC_KEEP]
    print(f'\n判定：糊 {len(blur)} 张（<{ratio * 100:.0f}%）'
          f'  软 {len(soft)} 张（{ratio * 100:.0f}–{review * 100:.0f}%）'
          f'  保留 {len(keep)} 张')
    if blur:
        print('待删：')
        for f in blur:
            print(f'  {f.name}  相对 {f.rel * 100:5.1f}%  {f.shutter}')
    if soft:
        print('偏软（不删，自己看要不要处理）：')
        for f in soft:
            print(f'  {f.name}  相对 {f.rel * 100:5.1f}%  {f.shutter}')
    unknown = [f for f in frames if not f.scored]
    if unknown:
        print(f'另有 {len(unknown)} 张解码失败、算不出锐度，已原样保留：')
        for f in unknown:
            print(f'  {f.name}')

    out_dir = Path(out_dir)
    csv_path = write_csv(frames, out_dir / '糊片报告.csv')
    sheet_path = write_contact_sheet(frames, out_dir / '糊片报告_联系表.jpg')
    print(f'\n报告：{csv_path}')
    print(f'联系表：{sheet_path}')

    if not apply:
        if not confirm:
            print('\ndry-run：没有删除任何文件。确认无误后加 --apply 再跑一次。')
            return 0
        if not blur:
            print('\n没有判为糊的帧，不需要删除。')
            return 0
        try:
            ans = input(f'\n确认把上面 {len(blur)} 张糊片移入回收站？输入 Y 回车，其他键取消：')
        except EOFError:
            print('\n没有收到确认输入，已取消。')
            return 0
        if ans.strip().lower() not in ('y', 'yes'):
            print('已取消，没有删除任何文件。')
            return 0

    targets = collect_deletions(blur, with_sidecar=with_sidecar)
    if not targets:
        print('\n没有需要删除的文件。')
        return 0
    print(f'\n删除 {len(targets)} 个文件（{"回收站" if recycle else "永久删除"}）…')
    done, failed = delete_files(targets, recycle=recycle)
    print(f'  完成 {len(done)} 个')
    if failed:
        print(f'  [失败] {len(failed)} 个：')
        for p in failed:
            print(f'    {p.name}')
        return 1
    return 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description='连拍糊片清理：组内相对锐度低于阈值的帧移入回收站',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('folder', nargs='?', default=None, help='照片文件夹')
    ap.add_argument('--after', default=None,
                    help='只处理这个文件之后的（按文件名编号，不含该文件），如 P1093278.RW2')
    ap.add_argument('--first', default=None, help='起始编号（含）')
    ap.add_argument('--last', default=None, help='结束编号（含）')
    ap.add_argument('--gap', type=float, default=DEFAULT_GAP,
                    help=f'同组最大间隔秒数（默认 {DEFAULT_GAP:g}）')
    ap.add_argument('--ratio', type=float, default=DEFAULT_RATIO,
                    help=f'删除线：相对锐度低于它判糊（默认 {DEFAULT_RATIO}）')
    ap.add_argument('--review', type=float, default=DEFAULT_REVIEW,
                    help=f'软帧上限：低于它只标记（默认 {DEFAULT_REVIEW}）')
    ap.add_argument('--apply', action='store_true', help='真的删除（默认只出报告）')
    ap.add_argument('--confirm', action='store_true',
                    help='出完报告后交互确认再删除（清糊片.bat 用这个）')
    ap.add_argument('--ask-after', action='store_true',
                    help='先问一句"只处理哪张之后"，直接回车=整个文件夹（清糊片.bat 用这个）')
    ap.add_argument('--usage', action='store_true', help='只打印简短用法')
    ap.add_argument('--hard', action='store_true', help='永久删除，不进回收站')
    ap.add_argument('--keep-jpg', action='store_true', help='保留同名 JPG，不跟着 RAW 一起删')
    ap.add_argument('--out', default=None, help='报告输出目录（默认 %TEMP%\\blur_cull\\<文件夹名>）')
    ap.add_argument('--quiet', action='store_true', help='不逐帧打印')
    return ap.parse_args(argv)


USAGE = """糊片清理：连拍里明显糊的帧移入回收站

把照片文件夹拖到 清糊片.bat 上，或者命令行：

    py blur_cull.py "D:\\Pictures\\raw\\109_PANA"                     只出报告
    py blur_cull.py "D:\\Pictures\\raw\\109_PANA" --after P1093278.RW2 只看这张之后
    py blur_cull.py "D:\\Pictures\\raw\\109_PANA" --apply              真删

默认 dry-run。判据：同场景连拍（默认 30 秒内、2 张以上）组内相对锐度
低于 --ratio（默认 0.80）判糊；单张孤立的照片不会被判糊。
"""


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.usage or args.folder is None:
        print(USAGE)
        return 1
    folder = Path(args.folder).expanduser()
    if not folder.is_dir():
        print(f'文件夹不存在：{folder}')
        return 2
    after = args.after
    if args.ask_after and not after:
        try:
            ans = input('可选：只清理某个文件之后的照片，输入文件名（如 P1093278.RW2），'
                        '直接回车=整个文件夹：')
        except EOFError:
            ans = ''
        after = ans.strip() or None
    frames = build_frames(folder, after=after, first=args.first, last=args.last,
                          verbose=not args.quiet)
    if not frames:
        print('没有可处理的 RAW 文件（可用 --after/--first/--last 缩小范围）')
        return 0
    out_dir = Path(args.out) if args.out else default_out_dir(folder)
    return report(frames, out_dir, apply=args.apply, recycle=not args.hard,
                  with_sidecar=not args.keep_jpg, gap=args.gap, ratio=args.ratio,
                  review=args.review, confirm=args.confirm)


if __name__ == '__main__':
    sys.exit(main())
