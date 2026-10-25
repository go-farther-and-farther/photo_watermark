# -*- coding: utf-8 -*-
"""blur_cull 糊片清理的纯逻辑与落盘测试

覆盖：
- 编号解析与 --after/--first/--last 筛选
- 按时间分组（含无 EXIF 时间的兜底）
- 组内相对锐度与三档判定（单帧组永不判糊）
- 拉普拉斯方差对模糊的单调性、对噪声的鲁棒性
- 同名 JPG 侧车联动删除、CSV/联系表落盘
"""
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

cv2 = pytest.importorskip("cv2")

import blur_cull as bc  # noqa: E402


# ---------- 工具 ----------

def _frame(name, ts, score):
    return bc.Frame(path=Path(name), ts=ts, score=score, scored=True)


def _unscored(name, ts):
    """解码失败的帧：score 留在 0，判定时必须原地保留。"""
    return bc.Frame(path=Path(name), ts=ts, score=0.0, scored=False)


def _texture(h=160, w=160, seed=0):
    """带细节的合成图：斜条纹 + 方块，模拟真实场景的高频结构。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    img = 128 + 60 * np.sin((xx + 2 * yy) / 3.0)
    img[h // 4:h // 2, w // 4:w // 2] = 220
    img += rng.normal(0, 6, img.shape)
    return np.clip(img, 0, 255)


# ---------- 编号与筛选 ----------

def test_frame_index_reads_trailing_number():
    assert bc.frame_index(Path("P1093278.RW2")) == 1093278
    assert bc.frame_index(Path("DSC_0178.NEF")) == 178
    assert bc.frame_index(Path("no_number.RW2")) is None


def test_filter_files_after_is_exclusive():
    paths = [Path(f"P1093{n:03d}.RW2") for n in (276, 278, 279, 305)]
    got = bc.filter_files(paths, after="P1093278.RW2")
    assert [p.name for p in got] == ["P1093279.RW2", "P1093305.RW2"]


def test_filter_files_first_last_inclusive():
    paths = [Path(f"P1093{n:03d}.RW2") for n in (276, 278, 279, 305)]
    got = bc.filter_files(paths, first="1093278", last="1093279")
    assert [p.name for p in got] == ["P1093278.RW2", "P1093279.RW2"]


def test_filter_files_without_filter_keeps_unnumbered():
    paths = [Path("a.RW2"), Path("b_01.RW2")]
    assert bc.filter_files(paths) == paths
    # 有筛选时无编号文件无法定位，直接排除
    assert bc.filter_files(paths, first=1) == [Path("b_01.RW2")]


# ---------- 分组 ----------

def test_group_by_time_splits_on_gap():
    fs = [_frame("a.RW2", 0.0, 100), _frame("b.RW2", 1.0, 90),
          _frame("c.RW2", 40.0, 80)]
    groups = bc.group_by_time(fs, gap=30)
    assert [len(g) for g in groups] == [2, 1]


def test_group_by_time_merges_slow_burst_within_gap():
    # 1/10s → 1/20s 这种同场景慢连拍，间隔 20 秒也要并到一组
    fs = [_frame(f"{i}.RW2", i * 20.0, 100 - i) for i in range(3)]
    assert [len(g) for g in bc.group_by_time(fs, gap=30)] == [3]


def test_group_by_time_missing_exif_is_singleton():
    fs = [_frame("a.RW2", 0.0, 100), _frame("b.RW2", None, 1.0), _frame("c.RW2", 1.0, 90)]
    groups = bc.group_by_time(fs, gap=30)
    assert any(len(g) == 1 and g[0].path.name == "b.RW2" for g in groups)
    assert sum(len(g) for g in groups) == 3


# ---------- 判定 ----------

def test_assign_relative_and_decide_three_levels():
    fs = [_frame("a.RW2", 0.0, 1000), _frame("b.RW2", 1.0, 850),
          _frame("c.RW2", 2.0, 750), _frame("d.RW2", 3.0, 500)]
    groups = bc.group_by_time(fs, gap=30)
    bc.assign_relative(groups)
    bc.decide(groups, ratio=0.80, review=0.90)
    assert [f.decision for f in fs] == [bc.DEC_KEEP, bc.DEC_SOFT, bc.DEC_BLUR, bc.DEC_BLUR]
    assert fs[0].rel == pytest.approx(1.0)


def test_single_frame_group_never_deleted():
    fs = [_frame("solo.RW2", 0.0, 12.0)]
    bc.assign_relative(bc.group_by_time(fs, gap=30))
    bc.decide(bc.group_by_time(fs, gap=30), ratio=0.80)
    assert bc.group_by_time(fs, gap=30)[0][0].decision == bc.DEC_KEEP


def test_unscored_frame_is_kept_and_excluded_from_best():
    """解码失败的帧不能因为 score=0 被判成最糊，也不能当组内基准。"""
    fs = [_frame("a.RW2", 0.0, 1000), _unscored("bad.RW2", 1.0), _frame("c.RW2", 2.0, 950)]
    groups = bc.group_by_time(fs, gap=30)
    bc.assign_relative(groups)
    bc.decide(groups, ratio=0.80)
    assert [f.decision for f in fs] == [bc.DEC_KEEP, bc.DEC_KEEP, bc.DEC_KEEP]
    assert fs[0].rel == pytest.approx(1.0)
    assert fs[1].rel == 0.0


def test_decide_threshold_is_configurable():
    fs = [_frame("a.RW2", 0.0, 1000), _frame("b.RW2", 1.0, 790)]
    groups = bc.group_by_time(fs, gap=30)
    bc.assign_relative(groups)
    bc.decide(groups, ratio=0.80, review=0.90)
    assert fs[1].decision == bc.DEC_BLUR
    bc.decide(groups, ratio=0.70, review=0.90)
    assert fs[1].decision == bc.DEC_SOFT


def test_exposure_conflict_detects_mixed_shutter():
    fs = [_frame("a.RW2", 0.0, 1), _frame("b.RW2", 1.0, 1)]
    fs[0].shutter, fs[1].shutter = "1/10", "1/20"
    assert bc.exposure_conflicts(fs)
    fs[1].shutter = "1/10"
    assert bc.exposure_conflicts(fs) == []


# ---------- 锐度指标 ----------

def test_sharpness_drops_with_blur():
    tex = _texture()
    blur = cv2.GaussianBlur(tex, (0, 0), 3.0)
    assert bc.sharpness(tex) > bc.sharpness(blur) * 1.5


def test_sharpness_survives_noise_added_to_both():
    """同一噪声水平下，糊帧必须仍然判得更低（不能靠噪点取胜）。"""
    rng = np.random.default_rng(7)
    tex = _texture()
    blur = cv2.GaussianBlur(tex, (0, 0), 3.0)
    noisy_sharp = tex + rng.normal(0, 8, tex.shape)
    noisy_blur = blur + rng.normal(0, 8, blur.shape)
    assert bc.sharpness(noisy_sharp) > bc.sharpness(noisy_blur)


# ---------- 侧车与删除 ----------

def test_sidecar_for_matches_case(tmp_path):
    raw = tmp_path / "P1093305.RW2"
    raw.write_bytes(b"x")
    assert bc.sidecar_for(raw) is None
    (tmp_path / "P1093305.JPG").write_bytes(b"x")
    assert bc.sidecar_for(raw).name == "P1093305.JPG"


def test_collect_deletions_includes_sidecar(tmp_path):
    raw = tmp_path / "P1093305.RW2"
    jpg = tmp_path / "P1093305.JPG"
    raw.write_bytes(b"x")
    jpg.write_bytes(b"x")
    f = bc.Frame(path=raw, decision=bc.DEC_BLUR)
    assert {p.name for p in bc.collect_deletions([f])} == {"P1093305.RW2", "P1093305.JPG"}
    assert [p.name for p in bc.collect_deletions([f], with_sidecar=False)] == ["P1093305.RW2"]


def test_delete_files_hard_removes(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("1")
    b.write_text("2")
    done, failed = bc.delete_files([a, b], recycle=False)
    assert len(done) == 2 and not failed
    assert not a.exists() and not b.exists()


# ---------- 进度回调（GUI 用） ----------

def test_score_frames_reports_progress(monkeypatch):
    """GUI 靠 on_progress 回填进度条；回调必须逐帧触发且顺序正确。"""
    monkeypatch.setattr(bc, "decode_gray", lambda p: np.zeros((8, 8), np.float32))
    monkeypatch.setattr(bc, "read_meta", lambda p, m=None: {"ts": 0.0, "shutter": "1/250"})
    monkeypatch.setattr(bc, "sharpness", lambda g: 123.0)
    frames = [bc.Frame(path=Path(f"a{i}.RW2")) for i in range(3)]
    seen = []
    bc.score_frames(frames, verbose=False,
                    on_progress=lambda i, t, f: seen.append((i, t, f.name)))
    assert seen == [(1, 3, "a0.RW2"), (2, 3, "a1.RW2"), (3, 3, "a2.RW2")]
    assert all(f.scored and f.score == 123.0 for f in frames)


def test_blur_gui_exposes_window_entry():
    """主程序按 `from blur_gui import open_blur_window` 懒加载，入口不能被改名。"""
    pytest.importorskip("tkinter")
    import blur_gui
    assert callable(blur_gui.open_blur_window)
    assert hasattr(blur_gui, "App")


# ---------- 报告落盘 ----------

def test_write_csv_and_sheet(tmp_path):
    # 用 JPG 侧车供联系表取缩略图，避免解码真实 RAW
    raw = tmp_path / "P1093305.RW2"
    raw.write_bytes(b"x")
    img = _texture(120, 160).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
    (tmp_path / "P1093305.JPG").write_bytes(buf.tobytes())

    frames = [bc.Frame(path=raw, ts=0.0, score=100, rel=0.5, group=1,
                       decision=bc.DEC_BLUR, shutter="1/10")]
    csv_path = bc.write_csv(frames, tmp_path / "out" / "r.csv")
    sheet_path = bc.write_contact_sheet(frames, tmp_path / "out" / "r.jpg")
    assert csv_path.exists() and sheet_path.exists()
    head = csv_path.read_text(encoding="utf-8-sig").splitlines()[0]
    assert "相对锐度%" in head and "判定" in head
    assert cv2.imread(str(sheet_path)) is not None
