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
