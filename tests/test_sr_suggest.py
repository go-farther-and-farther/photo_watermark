# -*- coding: utf-8 -*-
"""sr_suggest 连拍超分候选扫描与判定测试

设计文档：docs/superpowers/specs/2026-10-16-sr-suggest-design.md

用带 EXIF 拍摄时间的合成 JPG 连拍覆盖：
- 分组（快筛）、RAW+JPG 去重、组指纹、已完成清单
- 判定四分支：可超分 / 仅降噪（零位移）/ 抖动过大 / 内容在动 / 帧数不足
- scan_and_judge 端到端 + 取消保留部分结果
"""
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

cv2 = pytest.importorskip("cv2")
piexif = pytest.importorskip("piexif")

from test_sr import _base_image, _warp  # noqa: E402
from sr_suggest import (  # noqa: E402
    MIN_COMPOSE_FRAMES, dedup_raw_jpg, group_fingerprint, load_done_registry,
    record_done, judge_group, scan_photos, scan_and_judge,
)


# ---------- 测试工具 ----------

def _write_jpg(path, img, dt_sec):
    """写 8-bit JPG 并盖 EXIF DateTimeOriginal 时间戳（秒级）"""
    frame = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode('.jpg', bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    Path(path).write_bytes(buf.tobytes())
    dt = f"2026:10:16 12:00:{dt_sec:02d}"
    exif = piexif.dump({"Exif": {piexif.ExifIFD.DateTimeOriginal: dt.encode()}})
    piexif.insert(exif, str(path))


def _make_burst(folder, prefix, shifts, start_sec=0):
    """一个连拍组：每帧已知亚像素位移，时间逐秒递增"""
    base = _base_image()
    for i, (dx, dy) in enumerate(shifts):
        _write_jpg(folder / f"{prefix}_{i:02d}.jpg", _warp(base, dx, dy), start_sec + i)
    return sorted(folder.glob(f"{prefix}_*.jpg"))


NORMAL_SHIFTS = [(0.0, 0.0), (0.35, 0.6), (0.7, 0.1), (1.2, 1.5), (-0.5, 0.8), (0.2, -0.7)]


# ---------- 去重（纯函数） ----------

class TestDedupRawJpg:
    def test_same_stem_keeps_raw_only(self):
        files = [Path("a/DSC_0001.nef"), Path("a/DSC_0001.JPG"), Path("a/DSC_0002.jpg")]
        out = dedup_raw_jpg(files)
        assert out == [Path("a/DSC_0001.nef"), Path("a/DSC_0002.jpg")]

    def test_jpg_only_kept(self):
        files = [Path("a/DSC_0001.jpg"), Path("a/DSC_0002.JPG")]
        assert dedup_raw_jpg(files) == files


# ---------- 组指纹 ----------

class TestFingerprint:
    def test_stable_and_order_independent(self, tmp_path):
        a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
        a.write_bytes(b"x" * 10)
        b.write_bytes(b"y" * 20)
        assert group_fingerprint([a, b]) == group_fingerprint([b, a])
        assert len(group_fingerprint([a])) == 40  # sha1 hex

    def test_changes_with_content(self, tmp_path):
        a = tmp_path / "a.jpg"
        a.write_bytes(b"x" * 10)
        fp1 = group_fingerprint([a])
        a.write_bytes(b"x" * 11)
        assert group_fingerprint([a]) != fp1


# ---------- 已完成清单 ----------

class TestDoneRegistry:
    def test_record_and_load_roundtrip(self, tmp_path):
        jpg = tmp_path / "out_2x.jpg"
        jpg.write_bytes(b"j")
        record_done(tmp_path, "fp1", jpg, tif=None)
        reg = load_done_registry(tmp_path)
        assert "fp1" in reg and reg["fp1"]["jpg"] == str(jpg)

    def test_missing_registry_is_empty(self, tmp_path):
        assert load_done_registry(tmp_path) == {}


# ---------- 快筛分组 ----------

class TestScanPhotos:
    def test_burst_grouped_and_singles_counted(self, tmp_path):
        _make_burst(tmp_path, "burst", NORMAL_SHIFTS)          # 12:00:00-05，一组 6 张
        _write_jpg(tmp_path / "solo_a.jpg", _base_image(seed=1), 40)
        _write_jpg(tmp_path / "solo_b.jpg", _base_image(seed=2), 50)  # 与 a 间隔 10s
        groups, singles = scan_photos(tmp_path, gap=2.0, min_frames=3)
        assert singles == 2
        assert len(groups) == 1
        start_ts, files = groups[0]
        assert len(files) == 6
        assert all(f.name.startswith("burst_") for f in files)

    def test_gap_splits_groups(self, tmp_path):
        _make_burst(tmp_path, "g1", NORMAL_SHIFTS[:3], start_sec=0)   # 0,1,2 秒
        _make_burst(tmp_path, "g2", NORMAL_SHIFTS[:3], start_sec=10)  # 10,11,12 秒（间隔 8s > gap）
        groups, singles = scan_photos(tmp_path, gap=2.0, min_frames=3)
        assert len(groups) == 2
        assert singles == 0

    def test_small_group_counts_as_singles(self, tmp_path):
        _make_burst(tmp_path, "pair", NORMAL_SHIFTS[:2])
        groups, singles = scan_photos(tmp_path, gap=2.0, min_frames=3)
        assert groups == []
        assert singles == 2


# ---------- 深判 ----------

class TestJudgeGroup:
    def test_normal_burst_is_ok(self, tmp_path):
        files = _make_burst(tmp_path, "ok", NORMAL_SHIFTS)
        v = judge_group(files)
        assert v.status == "ok"
        # 剔除制：个别对齐残差大的帧可能被剔除（合成管线同样会滤），建议数 4-6
        assert MIN_COMPOSE_FRAMES <= v.suggested_frames <= 6

    def test_identical_frames_denoise_only(self, tmp_path):
        files = _make_burst(tmp_path, "tri", [(0.0, 0.0)] * 5)
        v = judge_group(files)
        assert v.status == "denoise_only"

    def test_too_few_frames(self, tmp_path):
        files = _make_burst(tmp_path, "two", NORMAL_SHIFTS[:2])
        v = judge_group(files)
        assert v.status == "no"
        assert "帧" in v.reason

    def test_three_frames_insufficient(self, tmp_path):
        """合成门槛 4 张起：3 帧组判帧数不足"""
        files = _make_burst(tmp_path, "thr", NORMAL_SHIFTS[:3])
        v = judge_group(files)
        assert v.status == "no"
        assert "帧" in v.reason

    def test_shift_dropped_instead_of_reject(self, tmp_path):
        """位移过大先剔除：抽样不足时全量重估，够 4 张好帧就判 ok 并报告剔除数"""
        base = _base_image()
        shifts = [(0.0, 0.0)] + [(0.3 + 0.1 * i, 0.5) for i in range(5)] + [(60.0, 0.0)] * 2
        files = _make_burst(tmp_path, "mix", shifts)
        v = judge_group(files, sample_frames=2, max_shift=50.0)
        assert v.status == "ok"
        assert v.suggested_frames == 6      # 8 帧剔除 2 张超限后剩 6
        assert "剔" in v.reason

    def test_shift_reject_after_full_recheck(self, tmp_path):
        """全量重估后可用帧仍不足 4 才判不可"""
        shifts = [(0.0, 0.0), (60.0, 0.0), (63.0, 3.0), (58.0, -4.0), (61.0, 5.0)]
        files = _make_burst(tmp_path, "shk5", shifts)
        v = judge_group(files, sample_frames=2, max_shift=50.0)
        assert v.status == "no"
        assert "位移" in v.reason

    def test_shift_too_large(self, tmp_path):
        shifts = [(0.0, 0.0), (9.0, 0.0), (10.0, 3.0), (11.0, -2.0), (12.0, 5.0)]
        files = _make_burst(tmp_path, "shk", shifts)
        v = judge_group(files, max_shift=5.0)
        assert v.status == "no"
        assert "位移" in v.reason

    def test_moving_content_rejected(self, tmp_path):
        # 静态背景 + 大幅移动的白色方块（对齐后差异区 >15%）：内容在动
        base = _base_image()
        for i, (sx, sy) in enumerate([(20, 20), (80, 50), (140, 80), (200, 110)]):
            img = base.copy()
            img[sy:sy + 100, sx:sx + 100] = 1.0
            _write_jpg(tmp_path / f"mv_{i:02d}.jpg", img, i)
        files = sorted(tmp_path.glob("mv_*.jpg"))
        v = judge_group(files)
        assert v.status == "no"
        assert "动" in v.reason

    def test_done_registry_hit(self, tmp_path):
        files = _make_burst(tmp_path, "fin", NORMAL_SHIFTS)
        outdir = tmp_path / "out"
        outdir.mkdir()
        jpg = outdir / "组01_120000_2x.jpg"
        jpg.write_bytes(b"j")
        record_done(outdir, group_fingerprint(files), jpg, tif=None)
        v = judge_group(files, done_registry=load_done_registry(outdir))
        assert v.status == "done"

    def test_done_but_output_deleted_falls_through(self, tmp_path):
        files = _make_burst(tmp_path, "fin2", NORMAL_SHIFTS)
        outdir = tmp_path / "out2"
        outdir.mkdir()
        jpg = outdir / "gone_2x.jpg"  # 记录了但文件不存在
        record_done(outdir, group_fingerprint(files), jpg, tif=None)
        v = judge_group(files, done_registry=load_done_registry(outdir))
        assert v.status == "ok"


# ---------- 端到端 ----------

class TestScanAndJudge:
    def test_mixed_folder_verdicts(self, tmp_path):
        _make_burst(tmp_path, "good", NORMAL_SHIFTS, start_sec=0)
        _make_burst(tmp_path, "tri", [(0.0, 0.0)] * 4, start_sec=20)
        _write_jpg(tmp_path / "solo.jpg", _base_image(seed=3), 30)
        verdicts, singles = scan_and_judge(tmp_path)
        statuses = sorted(v.status for v in verdicts)
        assert statuses == ["denoise_only", "ok"]
        assert singles == 1

    def test_cancel_keeps_partial_results(self, tmp_path):
        _make_burst(tmp_path, "g1", NORMAL_SHIFTS, start_sec=0)
        _make_burst(tmp_path, "g2", NORMAL_SHIFTS, start_sec=20)
        calls = {"n": 0}

        def cancel():
            calls["n"] += 1
            return calls["n"] > 1  # 第一组判完后取消

        verdicts, singles = scan_and_judge(tmp_path, cancel_cb=cancel)
        assert len(verdicts) == 1

    def test_progress_called(self, tmp_path):
        _make_burst(tmp_path, "g1", NORMAL_SHIFTS)
        msgs = []
        scan_and_judge(tmp_path, progress_cb=msgs.append)
        assert any("判定" in m for m in msgs)
