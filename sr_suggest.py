#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""连拍超分候选扫描与判定（设计：docs/superpowers/specs/2026-10-16-sr-suggest-design.md）

扫描 RAW+JPG 混合文件夹（几千张规模）：
  1) 快筛：线程池只读 EXIF 拍摄时间 → 按 gap 分组 → RAW/JPG 同名去重（RAW 优先）
  2) 深判：候选组时间均匀抽 K 帧、选最锐为参考做相位相关对齐，
     量三个指标（位移范围 / 对齐后内容运动 / 亚像素多样性）→ 四档判定
  3) 防重复：输出文件夹 .sr_done.json 清单（组指纹=文件名+大小哈希）

判定档位：ok 可超分 / denoise_only 仅降噪（三脚架近零位移）/
          no 不可（帧数不足、抖动过大、内容在动）/ done 已合成过
"""
import datetime
import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from align import estimate_shift, read_raw_as_gray
from burst_tools import _exif_datetime, _exif_subsecond

RAW_EXTS = ('.nef', '.nrw', '.arw', '.cr2', '.cr3', '.orf', '.raf', '.dng', '.rw2')
JPG_EXTS = ('.jpg', '.jpeg')
SCAN_EXTS = RAW_EXTS + JPG_EXTS

DONE_FILENAME = '.sr_done.json'

# 内容运动判定阈值（与 sr_process.reconstruct 一致：gamma 灰度差 >0.06 视为差异区，
# 差异区占比 >15% 的帧视为"内容在动"）
MOTION_DIFF = 0.06
MOTION_LIMIT = 0.15
# 亚像素多样性：可用帧小数位移两两最大欧氏距离低于该值视为近零位移（仅降噪）
SUBPIXEL_SPREAD = 0.1
# 合成门槛：不足 4 张不合成（中位数锚定也更稳）
MIN_COMPOSE_FRAMES = 4


@dataclass
class Verdict:
    """一组的判定结果

    status: 'ok' | 'denoise_only' | 'no' | 'done'
    """
    files: list
    start_time: float
    status: str
    reason: str
    suggested_frames: int = 0
    metrics: dict = field(default_factory=dict)


def dedup_raw_jpg(files):
    """同主文件名（stem）同时有 RAW 与 JPG 时只留 RAW（双格式拍摄的影子 JPG 去掉）"""
    by_stem = {}
    for f in files:
        by_stem.setdefault(f.stem.lower(), []).append(f)
    out = []
    for group in by_stem.values():
        raws = [f for f in group if f.suffix.lower() in RAW_EXTS]
        out.extend(sorted(raws) if raws else sorted(group))
    return sorted(out, key=lambda f: f.name)


def group_fingerprint(files):
    """组指纹：文件名+大小排序后哈希。与组号/扫描顺序/输出文件名无关"""
    names = sorted(f"{f.name}:{f.stat().st_size}" for f in files)
    return hashlib.sha1("\n".join(names).encode("utf-8")).hexdigest()


def load_done_registry(output_dir):
    """读输出文件夹的已完成清单；不存在或损坏返回空 dict"""
    p = Path(output_dir) / DONE_FILENAME
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record_done(output_dir, fingerprint, jpg, tif=None):
    """合成成功后写入清单（临时文件+原子替换）"""
    output_dir = Path(output_dir)
    reg = load_done_registry(output_dir)
    reg[fingerprint] = {
        "jpg": str(jpg),
        "tif": str(tif) if tif else None,
        "time": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    tmp = output_dir / (DONE_FILENAME + '.tmp')
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(output_dir / DONE_FILENAME)


def _read_timestamp(path):
    """读一张图的拍摄时间（含亚秒）；失败返回 None"""
    import exifread
    ts = _exif_datetime(path, exifread)
    if ts is None:
        return None
    return ts + _exif_subsecond(path, exifread), path


def scan_photos(folder, gap=2.0, min_frames=4, progress_cb=None, cancel_cb=None):
    """第一层·快筛：EXIF 拍摄时间分组（线程池并行，不解码像素）

    返回 (groups, singles_count)：groups = [(开始时间戳, [帧路径...]), ...] 按时间排序，
    只含去重后仍 ≥ min_frames 的组；不足的组计入 singles_count。
    """
    folder = Path(folder)
    files = sorted(p for p in folder.iterdir()
                   if p.is_file() and p.suffix.lower() in SCAN_EXTS)
    total = len(files)
    items = []
    pool = ThreadPoolExecutor(max_workers=8)
    try:
        futures = [pool.submit(_read_timestamp, p) for p in files]
        done = 0
        for fut in as_completed(futures):
            done += 1
            if cancel_cb and done % 64 == 0 and cancel_cb():
                break
            if progress_cb and (done % 50 == 0 or done == total):
                progress_cb(f"EXIF 扫描 {done}/{total}")
            r = fut.result()
            if r is not None:
                items.append(r)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    items.sort(key=lambda x: x[0])
    groups, singles = [], 0
    cur = [items[0]] if items else []
    for it in items[1:]:
        if it[0] - cur[-1][0] <= gap:
            cur.append(it)
        else:
            files_g = dedup_raw_jpg([p for _, p in cur])
            if len(files_g) >= min_frames:
                groups.append((cur[0][0], files_g))
            else:
                singles += len(files_g)
            cur = [it]
    if cur:
        files_g = dedup_raw_jpg([p for _, p in cur])
        if len(files_g) >= min_frames:
            groups.append((cur[0][0], files_g))
        else:
            singles += len(files_g)
    return groups, singles


def _judge_sample(files, sample, start_time, max_shift, allow_extend):
    """对给定帧子集做三指标判定；返回 Verdict。

    allow_extend=True 且位移/运动剔除后可用帧不足门槛时返回 None——
    说明抽样不足以定论，调用方应换全组帧重估（组内其余帧可能够用），
    与合成管线"剔除超限帧继续合成"的行为保持一致。
    """
    # 抽样子集里选最锐者为参考；半尺寸灰度解码 + 高频能量
    grays, scored = {}, []
    for p in sample:
        g = read_raw_as_gray(p, half_size=True)
        grays[p] = g
        scored.append((float((g - cv2.GaussianBlur(g, (0, 0), 2.0)).std()), p))
    scored.sort(key=lambda x: -x[0])
    ref = scored[0][1]
    ref_g = grays[ref]

    # 指标一：位移范围（半尺寸相位相关 → 全尺寸 ×2），超限帧剔除
    shift_full = {ref: (0.0, 0.0)}
    for p in sample:
        if p == ref:
            continue
        dx, dy, _ = estimate_shift(ref_g, grays[p])
        shift_full[p] = (dx * 2.0, dy * 2.0)
    dists = {p: math.hypot(*shift_full[p]) for p in sample}
    max_dist = max(dists.values())
    usable = [p for p in sample if dists[p] <= max_shift]
    if len(usable) < MIN_COMPOSE_FRAMES:
        if allow_extend:
            return None
        return Verdict(files, start_time, "no",
                       f"抖动过大：已剔除位移>{max_shift:.0f}px 的帧，可用仅 {len(usable)} 张"
                       f"（需≥{MIN_COMPOSE_FRAMES}）",
                       metrics={"max_shift_px": max_dist})

    # 指标二：内容运动（按位移 warp 回参考帧后比差异区占比），超限帧剔除
    h, w = ref_g.shape
    motion = {ref: 0.0}
    for p in usable:
        if p == ref:
            continue
        M = np.float32([[1, 0, -shift_full[p][0] / 2.0],
                        [0, 1, -shift_full[p][1] / 2.0]])
        aligned = cv2.warpAffine(grays[p], M, (w, h), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_CONSTANT, borderValue=-1.0)
        valid = aligned >= 0
        diff = np.abs(aligned - ref_g)
        motion[p] = float(((diff > MOTION_DIFF) & valid).sum() / max(valid.sum(), 1))
    still = [p for p in usable if motion[p] <= MOTION_LIMIT]
    if len(still) < MIN_COMPOSE_FRAMES:
        if allow_extend:
            return None
        worst = max(motion.values())
        return Verdict(files, start_time, "no",
                       f"画面内容在动：剔除后可用仅 {len(still)} 张（需≥{MIN_COMPOSE_FRAMES}，"
                       f"差异区最大 {worst:.0%}）",
                       metrics={"motion_frac": worst})

    # 指标三：亚像素多样性（小数位移两两最大欧氏距离）
    fracs = []
    for p in still:
        dx, dy = shift_full[p]
        fracs.append((dx - round(dx), dy - round(dy)))
    spread = max(math.hypot(ax - bx, ay - by)
                 for ax, ay in fracs for bx, by in fracs)
    if spread < SUBPIXEL_SPREAD:
        return Verdict(files, start_time, "denoise_only",
                       "三脚架近零位移：合成只降噪、不增加细节",
                       metrics={"subpixel_spread_px": spread})

    suggested = min(16, len(still))
    dropped = len(sample) - len(still)
    extra = f"，已剔除 {dropped} 帧" if dropped else ""
    return Verdict(files, start_time, "ok",
                   f"建议用 {suggested} 帧{extra}（画面移动 {max_dist:.1f}px，帧间抖动差异 {spread:.2f}px）",
                   suggested_frames=suggested,
                   metrics={"max_shift_px": max_dist, "subpixel_spread_px": spread})


def judge_group(files, done_registry=None, sample_frames=8, max_shift=150.0, start_time=0.0):
    """第二层·深判：一组连拍可否超分

    时间均匀抽 sample_frames 帧（不整组解码，控耗时）判定；位移/内容超限的帧
    按剔除制处理（与合成管线一致）——若抽样剔除后可用帧不足门槛，自动换全组
    帧重估一次再下结论，只有确实凑不齐 MIN_COMPOSE_FRAMES 张才判不可。
    """
    files = sorted(files)
    n = len(files)
    if n < MIN_COMPOSE_FRAMES:
        return Verdict(files, start_time, "no",
                       f"帧数不足（只有 {n} 张，需 ≥{MIN_COMPOSE_FRAMES}）")

    if done_registry:
        hit = done_registry.get(group_fingerprint(files))
        if hit and Path(hit.get("jpg", "")).exists():
            return Verdict(files, start_time, "done", f"已合成过：{Path(hit['jpg']).name}")

    # 抽样：时间均匀取 K 帧
    k = min(sample_frames, n)
    idxs = sorted(set(np.linspace(0, n - 1, k).round().astype(int).tolist()))
    sample = [files[i] for i in idxs]
    v = _judge_sample(files, sample, start_time, max_shift, allow_extend=(len(sample) < n))
    if v is None:
        # 抽样不足以下结论：全组重估（约 0.5-1s/帧，仅疑难组走到这里）
        v = _judge_sample(files, files, start_time, max_shift, allow_extend=False)
    return v


def scan_and_judge(input_folder, output_dir=None, gap=2.0, min_frames=4,
                   sample_frames=8, max_shift=50.0, progress_cb=None, cancel_cb=None):
    """入口：快筛 + 逐组深判。返回 (verdicts, singles_count)。

    cancel_cb() 为 True 时停止（已判定的结果保留）；progress_cb(str) 收进度消息。
    """
    registry = load_done_registry(output_dir) if output_dir else {}
    if progress_cb:
        progress_cb("第一层·EXIF 快筛...")
    groups, singles = scan_photos(input_folder, gap=gap, min_frames=min_frames,
                                  progress_cb=progress_cb, cancel_cb=cancel_cb)
    if progress_cb and groups:
        progress_cb(f"快筛完成：{len(groups)} 组候选，{singles} 张散照/小组")
    verdicts = []
    for i, (ts, files) in enumerate(groups, 1):
        if cancel_cb and cancel_cb():
            break
        if progress_cb:
            progress_cb(f"判定组 {i}/{len(groups)}：{len(files)} 帧")
        verdicts.append(judge_group(files, done_registry=registry,
                                    sample_frames=sample_frames, max_shift=max_shift,
                                    start_time=ts))
    return verdicts, singles
