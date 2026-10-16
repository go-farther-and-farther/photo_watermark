#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
连拍自动分组 + 选帧

按 EXIF 拍摄时间把文件夹里的 RAW 自动分组（组内间隔 < gap 秒），
每组按清晰度（高频能量，半尺寸解码）排序，选出最锐的 top-k 帧。
"""
import re
from pathlib import Path

import cv2
import numpy as np
import rawpy

RAW_EXTS = ('.nef', '.nrw', '.arw', '.cr2', '.cr3', '.orf', '.raf', '.dng', '.rw2')


def _exif_datetime(path, exifread_mod):
    """读取 EXIF 拍摄时间，返回秒级 float 时间戳；失败返回 None"""
    try:
        with open(str(path), 'rb') as f:
            tags = exifread_mod.process_file(f, details=False, stop_tag='EXIF DateTimeOriginal')
        for key in ('EXIF DateTimeOriginal', 'EXIF DateTimeDigitized', 'Image DateTime'):
            if key in tags:
                s = str(tags[key])
                m = re.match(r'(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})', s)
                if m:
                    import datetime
                    dt = datetime.datetime(*map(int, m.groups()))
                    return dt.timestamp() + getattr(tags.get(key), 'field_offset', 0) * 0
    except Exception:
        return None
    return None


def _exif_subsecond(path, exifread_mod):
    """读取毫秒字段（部分机身有 SubSecTime），返回 [0,1) 小数部分"""
    try:
        with open(str(path), 'rb') as f:
            tags = exifread_mod.process_file(f, details=False, stop_tag='EXIF SubSecTime')
        for key in ('EXIF SubSecTimeOriginal', 'EXIF SubSecTime', 'EXIF SubSecTimeDigitized'):
            if key in tags:
                v = str(tags[key]).strip()
                if v.isdigit():
                    return int(v[-3:]) / 1000.0
    except Exception:
        pass
    return 0.0


def scan_folder(folder, gap=2.0, min_frames=3):
    """
    扫描文件夹，按拍摄时间分组

    返回 [(组开始时间, [帧路径...]), ...] 按时间排序；不足 min_frames 的组丢弃。
    """
    import exifread
    folder = Path(folder)
    files = sorted(p for p in folder.iterdir()
                   if p.is_file() and p.suffix.lower() in RAW_EXTS)
    items = []
    for p in files:
        ts = _exif_datetime(p, exifread)
        if ts is None:
            print(f'  [跳过] 无 EXIF 时间: {p.name}')
            continue
        ts += _exif_subsecond(p, exifread)
        items.append((ts, p))
    if not items:
        return []
    items.sort(key=lambda x: x[0])
    groups, cur, cur_start = [], [items[0]], items[0][0]
    for ts, p in items[1:]:
        if ts - cur[-1][0] <= gap:
            cur.append((ts, p))
        else:
            if len(cur) >= min_frames:
                groups.append((cur_start, [p2 for _, p2 in cur]))
            cur, cur_start = [(ts, p)], ts
    if len(cur) >= min_frames:
        groups.append((cur_start, [p2 for _, p2 in cur]))
    return groups


def sharpness_rank(files):
    """按清晰度（半尺寸解码的高频能量）降序排列，返回 [(path, score), ...]"""
    scored = []
    for p in files:
        try:
            with rawpy.imread(str(p)) as raw:
                g = raw.postprocess(half_size=True, use_camera_wb=True, output_bps=8,
                                    no_auto_bright=True, gamma=(2.222, 4.5))
            g = g.mean(axis=2).astype(np.float32)
            hp = float((g - cv2.GaussianBlur(g, (0, 0), 2.0)).std())
            scored.append((p, hp))
        except Exception as e:
            print(f'  [跳过] 解码失败 {p.name}: {e}')
    scored.sort(key=lambda x: -x[1])
    return scored


def select_frames(files, top_k=8, drop_ratio=0.75):
    """
    选帧：最高分帧为 1.0，低于 drop_ratio×最高分的丢弃，最多保留 top_k 帧

    返回 [(path, score), ...] 按原拍摄顺序排列（便于稳定输出命名）。
    """
    scored = sharpness_rank(files)
    if not scored:
        return []
    best = scored[0][1]
    kept = [(p, s) for p, s in scored if s >= best * drop_ratio][:top_k]
    kept.sort(key=lambda x: files.index(x[0]))
    return kept


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='连拍分组预览（不处理）')
    ap.add_argument('folder')
    ap.add_argument('--gap', type=float, default=2.0, help='组内最大间隔秒数')
    ap.add_argument('--min-frames', type=int, default=3, help='每组最少帧数')
    ap.add_argument('--top-k', type=int, default=8, help='每组最多用帧数')
    args = ap.parse_args()
    groups = scan_folder(args.folder, gap=args.gap, min_frames=args.min_frames)
    print(f'共 {len(groups)} 组连拍:')
    import datetime
    for gi, (t0, frames) in enumerate(groups, 1):
        kept = select_frames(frames, top_k=args.top_k)
        tstr = datetime.datetime.fromtimestamp(t0).strftime('%H:%M:%S')
        print(f'  组{gi}: {tstr} 起, {len(frames)} 帧 -> 选 {len(kept)} 帧 '
              f'({frames[0].name} ~ {frames[-1].name})')
