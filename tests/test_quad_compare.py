# -*- coding: utf-8 -*-
"""quad_compare 单测：局部对齐 / 增益gamma色调对齐 / 网格拼图 / 端到端生成

全部用合成纹理（随机噪声 + 中频结构），不依赖真实照片；模板匹配对随机
纹理最敏感，正好覆盖对齐精度。中文文件名用 PIL 存盘顺便验证。
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))  # noqa: E402

import numpy as np
import pytest

cv2 = pytest.importorskip('cv2')
pytest.importorskip('PIL')

from quad_compare import (compose_grid, generate_comparisons,  # noqa: E402
                          local_align, match_tone, main as qc_main)

LUMA = np.array([0.0722, 0.7152, 0.2126], np.float32)   # BGR 亮度权重


def _texture(h, w, seed=7):
    """随机噪声加中频结构的合成纹理（模板匹配需要高频细节）"""
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    img = cv2.GaussianBlur(img, (0, 0), 2.0)
    big = cv2.GaussianBlur(img, (0, 0), 25)
    return np.clip(img.astype(np.float32) * 0.5 + big.astype(np.float32) * 1.2,
                   0, 255).astype(np.uint8)


def _luma_median(img):
    return float(np.median(img.astype(np.float32) @ LUMA))


def _setup_pair(tmp_path):
    """造一对同源测试图：参考图 + 平移压暗的对比面板，返回 (ref, alt) 路径"""
    base = _texture(600, 900, seed=5)
    ref_p = tmp_path / 'ref.jpg'
    cv2.imwrite(str(ref_p), base)
    moved = cv2.warpAffine(base, np.float32([[1, 0, 23], [0, 1, 14]]), (900, 600))
    alt = np.clip(moved.astype(np.float32) * 0.55, 0, 255).astype(np.uint8)
    alt_p = tmp_path / 'alt.jpg'
    cv2.imwrite(str(alt_p), alt)
    return ref_p, alt_p


class TestLocalAlign:
    def test_recovers_translation(self):
        """平移后的图里应找回裁块的原始位置（精度 = 降采样倍数 4px）"""
        base = _texture(500, 700)
        dx, dy = 37, -21
        moved = cv2.warpAffine(base, np.float32([[1, 0, dx], [0, 1, dy]]),
                               (700, 500))
        crop = moved[200:320, 300:460]
        x, y, score = local_align(crop, cv2.cvtColor(base, cv2.COLOR_BGR2GRAY))
        assert abs(x - (300 - dx)) <= 4, f'x 偏差 {x - (300 - dx)}'
        assert abs(y - (200 - dy)) <= 4, f'y 偏差 {y - (200 - dy)}'
        assert score > 0.5

    def test_survives_small_rotation(self):
        """帧间 0.1~1° 旋转是这套对齐存在的原因：0.5° 旋转后中心块仍能贴住"""
        base = _texture(600, 800)
        ang = 0.5
        M = cv2.getRotationMatrix2D((400, 300), ang, 1.0)
        rot = cv2.warpAffine(base, M, (800, 600))
        crop = rot[250:370, 350:510]                     # 靠近旋转中心，残差小
        x, y, score = local_align(crop, cv2.cvtColor(base, cv2.COLOR_BGR2GRAY))
        # 旋转使中心裁块内容相对原位置移动 tan(0.5°)*距离 ≈ 1~3px，容差 8px
        assert abs(x - 350) <= 8 and abs(y - 250) <= 8
        assert score > 0.4

    def test_rejects_oversize_template(self):
        base = _texture(200, 300)
        with pytest.raises(ValueError):
            local_align(base, cv2.cvtColor(_texture(100, 100), cv2.COLOR_BGR2GRAY))


class TestMatchTone:
    def test_recovers_gain_and_gamma(self):
        """把参考加亮 1.5 倍再压 gamma 后，应拉回参考的中位亮度"""
        ref = _texture(240, 320, seed=3)
        ref = np.clip(ref.astype(np.float32) * 0.6, 20, 165).astype(np.uint8)
        alt = (np.clip((ref.astype(np.float32) / 255.0) ** 0.75, 0, 1) * 255
               ).astype(np.uint8)                        # 明显更亮更灰
        assert abs(_luma_median(alt) - _luma_median(ref)) > 20   # 前提：确实偏了
        fixed = match_tone(alt, ref)
        assert fixed.dtype == np.uint8 and fixed.shape == alt.shape
        assert abs(_luma_median(fixed) - _luma_median(ref)) < 12, \
            f'中位亮度 {_luma_median(fixed):.1f} vs 参考 {_luma_median(ref):.1f}'

    def test_preserves_channels_together(self):
        """增益+gamma 是全通道同一条单调曲线：三通道中位应一起回到参考"""
        rng = np.random.default_rng(11)
        grad = np.linspace(30, 200, 240 * 320, dtype=np.float32).reshape(240, 320)
        ref = np.stack([grad, grad * 0.8, grad * 0.6], -1).astype(np.uint8)
        alt = np.stack([grad * 1.6, grad * 1.3, grad * 1.0], -1)
        alt = np.clip(alt, 0, 255).astype(np.uint8)
        fixed = match_tone(alt, ref)
        for c, name in enumerate('BGR'):
            m_ref = float(np.median(ref[..., c]))
            m_fix = float(np.median(fixed[..., c]))
            assert abs(m_fix - m_ref) < 15, f'{name} 通道中位 {m_fix:.0f} vs {m_ref:.0f}'


class TestComposeGrid:
    def test_four_panels_layout(self, tmp_path):
        panels = [(f'面板{i}', _texture(120, 200, seed=i)) for i in range(4)]
        img = compose_grid(panels, '测试标题')
        pad, top, lab, bot = 18, 58, 34, 44
        assert img.size == (200 * 2 + pad * 3, 120 * 2 + top + lab * 2 + bot + pad)
        out = tmp_path / '四格_测试.png'                 # 中文文件名能存
        img.save(out)
        assert out.exists() and out.stat().st_size > 1000

    def test_two_panels_one_row(self):
        panels = [('甲', _texture(80, 100, seed=1)), ('乙', _texture(80, 100, seed=2))]
        img = compose_grid(panels)
        assert img.size == (100 * 2 + 18 * 3, 80 + 58 + 34 + 44 + 18)

    def test_rejects_mismatched_sizes(self):
        with pytest.raises(ValueError):
            compose_grid([('a', _texture(80, 100, seed=1)), ('b', _texture(90, 100, seed=2))])


class TestMatchToneEdge:
    def test_constant_crop_no_nan(self):
        """常数裁块对纯白参考：gamma 拟合是 0/0=NaN，必须退回 g=1 而不是
        输出平台相关的垃圾值（NaN clip 不掉，astype(uint8) 结果不定）"""
        ref = np.full((80, 100, 3), 255, np.uint8)
        crop = np.full((80, 100, 3), 128, np.uint8)
        fixed = match_tone(crop, ref)
        assert fixed.dtype == np.uint8 and np.isfinite(fixed.astype(np.float64)).all()
        assert fixed.shape == crop.shape


class TestGenerateComparisons:
    def test_end_to_end(self, tmp_path):
        ref_p, alt_p = _setup_pair(tmp_path)
        out_dir = tmp_path / '输出目录'                  # 中文目录名
        progress = []
        outs = generate_comparisons(
            ref_p, [('单帧', alt_p)], [('中心', 0.5, 0.5)], out_dir,
            crop_wh=(240, 160), on_progress=lambda i, t, n, p: progress.append((i, t, n)))
        assert len(outs) == 1 and outs[0].exists()
        assert outs[0].name == '对比_四格_中心.png'
        from PIL import Image
        with Image.open(outs[0]) as im:
            assert im.size == (240 * 2 + 18 * 3, 160 + 58 + 34 + 44 + 18)
        assert progress == [(1, 1, '中心')]

    def test_smaller_panel_image_is_handled(self, tmp_path):
        """面板图比参考图略小（尺寸不同源）时按自身坐标裁，不越界不崩"""
        ref_p, alt_p = _setup_pair(tmp_path)
        small_p = tmp_path / 'small.jpg'
        cv2.imwrite(str(small_p), _texture(590, 880, seed=9))
        outs = generate_comparisons(ref_p, [('小图', small_p)], [('右下', 0.9, 0.9)],
                                    tmp_path / 'out2', crop_wh=(200, 140),
                                    verbose=False)
        assert len(outs) == 1

    def test_bad_spot_does_not_abort_batch(self, tmp_path, capsys):
        ref_p, alt_p = _setup_pair(tmp_path)
        outs = generate_comparisons(
            ref_p, [('单帧', alt_p)],
            [('好点', 0.5, 0.5), ('坏点', 5.0, 0.5)],    # 坏点会被钳回边界，仍应出图
            tmp_path / 'out3', crop_wh=(240, 160), verbose=False)
        assert len(outs) == 2

    def test_failed_spot_reports_none_to_progress(self, tmp_path):
        """存盘失败（点名含 Windows 非法字符 ?）时 on_progress 必须收到 None，
        且整批继续——契约见 docstring"""
        ref_p, alt_p = _setup_pair(tmp_path)
        seen = []
        outs = generate_comparisons(
            ref_p, [('单帧', alt_p)], [('非法?名', 0.5, 0.5), ('正常', 0.5, 0.5)],
            tmp_path / 'out_none', crop_wh=(240, 160), verbose=False,
            on_progress=lambda i, t, n, p: seen.append((n, p)))
        assert len(outs) == 1 and outs[0].name == '对比_四格_正常.png'
        names = {n: p for n, p in seen}
        assert names['非法?名'] is None and names['正常'] is not None

    def test_panel_smaller_than_crop_errors(self, tmp_path):
        ref_p, _ = _setup_pair(tmp_path)
        tiny_p = tmp_path / 'tiny.jpg'
        cv2.imwrite(str(tiny_p), _texture(100, 120, seed=2))
        with pytest.raises(ValueError, match='比裁块'):
            generate_comparisons(ref_p, [('tiny', tiny_p)], [('p', 0.5, 0.5)],
                                 tmp_path / 'out4', crop_wh=(240, 160), verbose=False)


class TestCli:
    def test_cli_end_to_end(self, tmp_path, capsys):
        ref_p, alt_p = _setup_pair(tmp_path)
        out = tmp_path / 'cli_out'
        rc = qc_main(['--ref', str(ref_p),
                      '--panel', f'单帧={alt_p}',
                      '--spot', '路口=0.5,0.5',
                      '--out', str(out), '--crop', '240x160', '--quiet'])
        assert rc == 0
        files = list(out.glob('*.png'))
        assert len(files) == 1 and files[0].name == '对比_四格_路口.png'

    def test_cli_rejects_bad_spot(self, tmp_path):
        ref_p, alt_p = _setup_pair(tmp_path)
        with pytest.raises(SystemExit):
            qc_main(['--ref', str(ref_p), '--panel', f'单帧={alt_p}',
                     '--spot', '坏=1.5,0.5', '--out', str(tmp_path)])
