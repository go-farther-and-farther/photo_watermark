# -*- coding: utf-8 -*-
"""单应对齐 + 噪声感知加权测试（设计：docs/super_res/单应对齐与噪声加权-设计-2026-10-16.md）

- _homography_ok：全尺寸 3×3 warp 解析门控（旋转/平移/缩放/翻转/透视位移）
- _proj_coords vs _affine_coords：透视项为零时逐位一致（快路径等价性）
- _noise_weights：实测残差定权（逆平方、截断 [0.5,2]、NaN、参考帧取中位）
- _refine_homography：合成图已知单应的恢复精度（欧氏模型留不住的透视项）
"""
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

cv2 = pytest.importorskip("cv2")

import sr_process  # noqa: E402


def _texture(size=512, seed=7):
    """多尺度平滑纹理（ECC 需要宽谱梯度内容）"""
    rng = np.random.default_rng(seed)
    n = rng.uniform(0.0, 1.0, (size, size)).astype(np.float32)
    fine = cv2.GaussianBlur(n, (0, 0), 1.5)
    coarse = cv2.GaussianBlur(n, (0, 0), 9.0)
    tex = 0.6 * fine + 0.4 * coarse
    tex -= tex.min()
    return (tex / tex.max()).astype(np.float32)


def _corner_disp(H1, H2, w, h):
    """两组 warp 对四角投影的最大位移（同方向：ref 坐标 -> frame 坐标）"""
    corners = np.array([[0, 0, 1], [w, 0, 1], [0, h, 1], [w, h, 1]], dtype=np.float64).T
    p1 = H1 @ corners
    p2 = H2 @ corners
    p1 = p1[:2] / p1[2]
    p2 = p2[:2] / p2[2]
    return float(np.max(np.linalg.norm(p1 - p2, axis=0)))


def _euclid3(a_deg, tx, ty):
    a = np.radians(a_deg)
    return np.array([[np.cos(a), -np.sin(a), tx],
                     [np.sin(a), np.cos(a), ty],
                     [0.0, 0.0, 1.0]], dtype=np.float64)


class TestHomographyOk:
    """全尺寸门控：旋转≤0.5°、平移≤60px、奇异值比[0.97,1.03]、det>0、透视角点位移≤0.5%短边"""

    def test_identity_accepts(self):
        ok, why = sr_process._homography_ok(np.eye(3), (512, 512))
        assert ok, why

    def test_small_motion_accepts(self):
        ok, why = sr_process._homography_ok(_euclid3(0.3, 10.0, -8.0), (512, 512))
        assert ok, why

    def test_excess_rotation_rejected(self):
        ok, why = sr_process._homography_ok(_euclid3(2.0, 1.0, 1.0), (512, 512))
        assert not ok and '旋转' in why

    def test_excess_shift_rejected(self):
        ok, why = sr_process._homography_ok(_euclid3(0.0, 100.0, 0.0), (512, 512))
        assert not ok and '平移' in why

    def test_mirror_rejected(self):
        H = np.array([[-1.0, 0, 5], [0, 1.0, 0], [0, 0, 1.0]])
        ok, why = sr_process._homography_ok(H, (512, 512))
        assert not ok and '翻转' in why

    def test_scale_jump_rejected(self):
        H = _euclid3(0.0, 0, 0).copy()
        H[0, 0] = H[1, 1] = 1.10
        ok, why = sr_process._homography_ok(H, (512, 512))
        assert not ok and '缩放' in why

    def test_strong_perspective_rejected(self):
        # 透视项逐步加大，直到角点位移确实超过 0.5%·短边，门控必须拒绝
        limit = 0.005 * 512
        H = _euclid3(0.1, 2.0, 1.0)
        H[2, 0] = 8e-5          # 角点位移远超 2.56px
        disp = _corner_disp(H, np.vstack([H[:2], [0, 0, 1.0]]), 512, 512)
        assert disp > limit
        ok, why = sr_process._homography_ok(H, (512, 512))
        assert not ok and '透视' in why

    def test_mild_perspective_accepts(self):
        H = _euclid3(0.1, 2.0, 1.0)
        H[2, 0] = 4e-6          # 角点位移 ~1.5px，在容差内但不贴边
        disp = _corner_disp(H, np.vstack([H[:2], [0, 0, 1.0]]), 512, 512)
        assert 0.5 < disp < 0.8 * 0.005 * 512
        ok, why = sr_process._homography_ok(H, (512, 512))
        assert ok, why


class TestProjCoordsEquivalence:
    """透视项为零的 H 与仿射 A 必须给出逐位一致的目标坐标（融合快路径等价）"""

    def test_affine_bitwise_equal(self):
        rng = np.random.default_rng(3)
        SX = rng.uniform(-100, 900, (37, 53)).astype(np.float32)
        SY = rng.uniform(-100, 900, (37, 53)).astype(np.float32)
        A = np.array([[0.99998, 0.0011, 12.37],
                      [-0.0011, 0.99998, -4.81]], dtype=np.float64)
        H = np.vstack([A, [0.0, 0.0, 1.0]])
        mx_a, my_a = sr_process._affine_coords(A, SX, SY)
        mx_p, my_p = sr_process._proj_coords(H, SX, SY)
        assert np.array_equal(mx_a, mx_p)
        assert np.array_equal(my_a, my_p)

    def test_projective_matches_formula(self):
        rng = np.random.default_rng(4)
        SX = rng.uniform(0, 512, (11, 17)).astype(np.float64)
        SY = rng.uniform(0, 512, (11, 17)).astype(np.float64)
        H = _euclid3(0.05, 3.0, -1.0)
        H[2, 0] = 5e-6
        mx, my = sr_process._proj_coords(H, SX, SY)
        d = H[2, 0] * SX + H[2, 1] * SY + H[2, 2]
        assert np.allclose(mx, (H[0, 0] * SX + H[0, 1] * SY + H[0, 2]) / d)
        assert np.allclose(my, (H[1, 0] * SX + H[1, 1] * SY + H[1, 2]) / d)
        assert not np.allclose(mx, H[0, 0] * SX + H[0, 1] * SY + H[0, 2])


class TestNoiseWeights:
    """w=1/(rms²+floor)，相对中位截断 [0.5,2]；NaN→1；参考帧取其余帧中位"""

    def test_ordering_and_range(self):
        blur_rms = np.array([np.nan, 0.010, 0.020, 0.030], dtype=np.float32)
        w_usable, w_ref = sr_process._noise_weights(blur_rms, [1, 2, 3], ref_idx=0)
        assert w_usable[0] > w_usable[1] > w_usable[2]      # 越锐权重越高
        assert np.all(w_usable >= 0.5) and np.all(w_usable <= 2.0)
        assert w_ref == pytest.approx(float(np.median(w_usable)))

    def test_clamped_extremes(self):
        # floor=median(r²) 时权重比数学上恒 ≤2（上截断是不可达护栏），
        # 下截断 0.5 会真实咬合：糊帧压到下界，剔除仍由 RMS 门限负责
        blur_rms = np.array([np.nan, 0.001, 0.010, 0.012, 0.014, 0.060], dtype=np.float32)
        w_usable, _ = sr_process._noise_weights(blur_rms, [1, 2, 3, 4, 5], ref_idx=0)
        assert w_usable.max() <= 2.0
        assert w_usable.min() == pytest.approx(0.5)
        assert w_usable[0] > w_usable[1] > w_usable[2] > w_usable[3]

    def test_all_nan_gives_uniform(self):
        blur_rms = np.full(4, np.nan, dtype=np.float32)
        w_usable, w_ref = sr_process._noise_weights(blur_rms, [0, 1, 2], ref_idx=3)
        assert np.allclose(w_usable, 1.0)
        assert w_ref == pytest.approx(1.0)

    def test_single_frame(self):
        blur_rms = np.array([np.nan, 0.02], dtype=np.float32)
        w_usable, w_ref = sr_process._noise_weights(blur_rms, [1], ref_idx=0)
        assert w_usable[0] == pytest.approx(1.0)
        assert w_ref == pytest.approx(1.0)


class TestRefineHomography:
    """合成图已知单应恢复：透视项 ~0.7px@角点，欧氏模型留不住，单应必须拿回来"""

    def _synth_pair(self, p0, a_deg=0.15, tx=3.7, ty=-2.4, size=512, seed=7):
        ref = _texture(size, seed)
        H_gt = _euclid3(a_deg, tx, ty)
        H_gt[2, 0] = p0
        frm = cv2.warpPerspective(ref, H_gt, (size, size),
                                  flags=cv2.INTER_CUBIC,
                                  borderMode=cv2.BORDER_REPLICATE)
        return ref, frm, H_gt

    def test_recovers_known_homography(self):
        ref, frm, H_gt = self._synth_pair(p0=6e-6)
        dx_full, dy_full = 3.7 * 2, -2.4 * 2        # 传入的是全分辨率口径
        H, fx0, fy0, x0, y0 = sr_process._refine_homography(ref, frm, dx_full, dy_full)
        Th = np.array([[1, 0, fx0], [0, 1, fy0], [0, 0, 1]], dtype=np.float64)
        To = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], dtype=np.float64)
        Ph = Th @ H @ To                             # 半尺寸坐标系（即本测试的 512 空间）
        disp = _corner_disp(Ph, H_gt, 512, 512)
        assert disp < 0.5, f'单应恢复角点误差 {disp:.3f}px 应 <0.5px'

    def test_beats_euclidean_under_perspective(self):
        ref, frm, H_gt = self._synth_pair(p0=6e-6)
        dx_full, dy_full = 3.7 * 2, -2.4 * 2
        H, fx0, fy0, x0, y0 = sr_process._refine_homography(ref, frm, dx_full, dy_full)
        Th = np.array([[1, 0, fx0], [0, 1, fy0], [0, 0, 1]], dtype=np.float64)
        To = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], dtype=np.float64)
        Ph = Th @ H @ To
        E, ex0, ey0, ex_, ey_ = sr_process._refine_euclidean(ref, frm, 3.7 * 2, -2.4 * 2)
        E3 = np.vstack([E, [0.0, 0.0, 1.0]])
        PE = (np.array([[1, 0, ex0], [0, 1, ey0], [0, 0, 1]], dtype=np.float64)
              @ E3 @ np.array([[1, 0, -ex_], [0, 1, -ey_], [0, 0, 1]], dtype=np.float64))
        d_homo = _corner_disp(Ph, H_gt, 512, 512)
        d_eucl = _corner_disp(PE, H_gt, 512, 512)
        assert d_eucl > d_homo, f'有透视时欧氏({d_eucl:.3f}px)应差于单应({d_homo:.3f}px)'

    def test_translation_only_pair(self):
        ref, frm, _ = self._synth_pair(p0=0.0, a_deg=0.0, tx=5.3, ty=1.9)
        H, *_ = sr_process._refine_homography(ref, frm, 5.3 * 2, 1.9 * 2)
        rot = abs(np.degrees(np.arctan2(H[1, 0], H[0, 0])))
        persp = abs(H[2, 0]) * 512 + abs(H[2, 1]) * 512
        assert rot < 0.02
        assert persp < 5e-3                          # 无透视输入不造透视


class TestLocalField:
    """局部偏移场：分块相位相关测残差平移（视差校正的基础）"""

    def _tex(self, size=512, seed=11):
        rng = np.random.default_rng(seed)
        n = rng.uniform(0, 1, (size, size)).astype(np.float32)
        t = cv2.GaussianBlur(n, (0, 0), 1.5)
        t -= t.min()
        return t / t.max()

    def test_recovers_piecewise_shift(self):
        ref = self._tex()
        # 左半 tiles 内容整体移 (+2,+1)，右半不动 -> 场应分别恢复
        ali = np.zeros_like(ref)
        M = np.array([[1, 0, 2], [0, 1, 1]], dtype=np.float32)
        ali[:, :256] = cv2.warpAffine(ref[:, :256], M, (256, 512),
                                      borderMode=cv2.BORDER_REPLICATE)
        ali[:, 256:] = ref[:, 256:]
        field, valid = sr_process._local_field(ref, ali, tile=128, max_local=6.0)
        assert valid.shape == (4, 4)
        assert valid[:, :2].all() and valid[:, 2:].all()
        left = field[:, :2]      # (J,2,2)
        assert np.allclose(left[..., 0], 2.0, atol=0.35), left[..., 0]
        assert np.allclose(left[..., 1], 1.0, atol=0.35), left[..., 1]
        assert np.allclose(field[:, 2:], 0.0, atol=0.35)

    def test_flat_tiles_invalid(self):
        ref = self._tex()
        ref[:256, :] = 0.5       # 上半全平 -> 上半 tiles 应无效
        ali = cv2.warpAffine(ref, np.array([[1, 0, 1.5], [0, 1, 0.5]], np.float32),
                             (512, 512), borderMode=cv2.BORDER_REPLICATE)
        field, valid = sr_process._local_field(ref, ali, tile=128, max_local=6.0)
        assert not valid[:2].any()
        assert valid[2:].all()

    def test_clamped_to_max_local(self):
        ref = self._tex()
        ali = cv2.warpAffine(ref, np.array([[1, 0, 30], [0, 1, 0]], np.float32),
                             (512, 512), borderMode=cv2.BORDER_REPLICATE)
        field, valid = sr_process._local_field(ref, ali, tile=128, max_local=6.0)
        assert np.all(np.abs(field) <= 6.0 + 1e-6)


class TestApplyField:
    """融合采样坐标叠加偏移场：双线性 + 网格外常数外推（全尺寸坐标口径）"""

    def test_zero_field_identity(self):
        SX, SY = np.meshgrid(np.arange(0, 400, 7.3), np.arange(0, 300, 5.1))
        off = np.zeros((3, 4, 2), dtype=np.float32)
        dx, dy = sr_process._apply_field(off, SX, SY, tile_full=256)
        assert np.array_equal(dx, np.zeros_like(SX))
        assert np.array_equal(dy, np.zeros_like(SY))

    def test_constant_field_added(self):
        SX = np.array([[100.0, 900.0]])
        SY = np.array([[50.0, 50.0]])
        off = np.zeros((4, 4, 2), dtype=np.float32)
        off[:, :] = (1.5, -2.0)  # 半尺寸单位
        dx, dy = sr_process._apply_field(off, SX, SY, tile_full=256)
        assert np.allclose(dx, 3.0)    # x2 转全尺寸
        assert np.allclose(dy, -4.0)

    def test_bilinear_between_regions(self):
        # 场左半=+2、右半=+4（半尺寸），控制点中点插值应为 +3（全尺寸 6）
        off = np.zeros((2, 4, 2), dtype=np.float32)
        off[:, :2] = (2.0, 0.0)
        off[:, 2:] = (4.0, 0.0)
        SX = np.array([[512.0]])   # tile_full=256 -> gx = 512/256-0.5 = 1.5（列1、2正中）
        dx, _ = sr_process._apply_field(off, SX, np.array([[100.0]]), tile_full=256)
        assert np.allclose(dx, 6.0), dx


# ======================================================================
# 单帧锐度门控（设计：docs/super_res/单帧锐度门控-设计-2026-10-17.md）
# ======================================================================

from align import sharpness_score  # noqa: E402
from pathlib import Path  # noqa: E402


class TestSharpnessScore:
    """拉普拉斯方差锐度（半尺寸灰度上计算，抗噪靠解码端 2x2 像素合并）"""

    def test_blur_monotonic(self):
        tex = _texture(256)
        s_raw = sharpness_score(tex)
        s_b2 = sharpness_score(cv2.GaussianBlur(tex, (0, 0), 2.0))
        s_b5 = sharpness_score(cv2.GaussianBlur(tex, (0, 0), 5.0))
        assert s_raw > s_b2 > s_b5 > 0
        # 最重模糊掉进门限（0.35×最佳）以下——门控能识别
        assert s_b5 < s_raw * 0.35

    def test_noise_robust(self):
        # 适度噪声不该翻转锐/糊排序（抗噪靠解码端像素合并，
        # 见 sharpness_score docstring 的实测数据）
        rng = np.random.default_rng(3)
        tex = _texture(256)
        noise = rng.normal(0, 0.02, tex.shape).astype(np.float32)
        s_sharp_noisy = sharpness_score(tex + noise)
        s_blur_noisy = sharpness_score(cv2.GaussianBlur(tex, (0, 0), 3.0) + noise)
        assert s_sharp_noisy > s_blur_noisy

    def test_flat_image_near_zero(self):
        assert sharpness_score(np.full((128, 128), 0.5, np.float32)) < 1.0


class TestSelectBestFramesSharpGate:
    """选帧阶段糊片剔除：相对组内最佳的比例门限 + 参考帧豁免 + 帧数保底"""

    @staticmethod
    def _make(n, sharp_vals, ref_idx=0):
        files = [Path(f"DSC_{i:04d}.NEF") for i in range(n)]
        shifts = [(0.3 * i, -0.2 * i) for i in range(n)]   # 全部小位移
        return files, shifts, ref_idx, list(sharp_vals)

    def test_blurry_dropped(self):
        files, shifts, ref, sharp = self._make(
            10, [9000, 8000, 9000, 8500, 300, 400, 350, 9000, 8800, 8600])
        idx, fs, ss, ri = sr_process.select_best_frames(
            files, shifts, ref, max_frames=16, sharp=sharp)
        names = {f.name for f in fs}
        assert len(fs) == 7                      # 3 张糊片被剔
        assert not names & {"DSC_0004.NEF", "DSC_0005.NEF", "DSC_0006.NEF"}
        assert fs[ri].name == "DSC_0000.NEF"     # 参考帧重映射正确
        assert len(ss) == len(fs) == len(idx)

    def test_ref_exempt_even_if_blurry(self):
        files, shifts, ref, sharp = self._make(
            10, [100, 9000, 8000, 9000, 8500, 300, 400, 350, 8800, 8600])
        idx, fs, ss, ri = sr_process.select_best_frames(
            files, shifts, ref, max_frames=16, sharp=sharp)
        assert fs[ri].name == "DSC_0000.NEF"     # 参考帧糊也保留（下游加权兜底）
        assert "DSC_0005.NEF" not in {f.name for f in fs}

    def test_floor_no_drop(self):
        # 剔除后只剩 3 帧 < 保底 6：整组不启用门控
        files, shifts, ref, sharp = self._make(
            8, [9000, 100, 200, 150, 100, 200, 150, 9000])
        idx, fs, ss, ri = sr_process.select_best_frames(
            files, shifts, ref, max_frames=16, sharp=sharp)
        assert len(fs) == 8

    def test_small_group_untouched(self):
        files, shifts, ref, sharp = self._make(6, [9000, 100, 100, 100, 100, 100])
        idx, fs, ss, ri = sr_process.select_best_frames(
            files, shifts, ref, max_frames=16, sharp=sharp)
        assert len(fs) == 6                      # 候选 ≤ 保底帧数，不启用

    def test_gate_off_and_none(self):
        files, shifts, ref, sharp = self._make(
            10, [9000, 8000, 9000, 8500, 300, 400, 350, 9000, 8800, 8600])
        for kw in ({"sharp": sharp, "sharp_gate": False}, {"sharp": None}):
            idx, fs, ss, ri = sr_process.select_best_frames(
                files, shifts, ref, max_frames=16, **kw)
            assert len(fs) == 10

    def test_combined_with_displacement_filter(self):
        # 大组（>max_frames）：锐度门控 + 位移过滤叠加，参考帧始终保留
        files, shifts, ref, sharp = self._make(
            20, [9000] * 20)
        for i in (3, 7, 11):                     # 3 张糊片
            sharp[i] = 200
        for i in (5, 15):                        # 2 张大位移
            shifts[i] = (400.0, 0.0)
        idx, fs, ss, ri = sr_process.select_best_frames(
            files, shifts, ref, max_frames=12, max_shift=150.0, sharp=sharp)
        names = [f.name for f in fs]
        assert len(fs) == 12                     # 20 - 3(糊) - 2(位移) = 15 > 12，取 12
        assert "DSC_0000.NEF" in names and fs[ri].name == "DSC_0000.NEF"
        assert not {"DSC_0003.NEF", "DSC_0007.NEF", "DSC_0011.NEF"} & set(names)
