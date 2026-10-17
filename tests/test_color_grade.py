# -*- coding: utf-8 -*-
"""调色模板测试：线性光数学、模板读写、导出链路（只作用于照片本体）"""
import configparser
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import color_grade as cg  # noqa: E402


def _grad(h=32, w=32):
    x = np.linspace(0, 1, w, dtype=np.float32)
    return np.repeat(np.repeat(x[None, :, None], h, axis=0), 3, axis=2)


class TestGradeMath:
    def test_strength_zero_is_identity(self):
        a = np.random.default_rng(0).random((16, 16, 3)).astype(np.float32)
        out = cg.grade_array(a, cg.BUILTIN_TEMPLATES['视频感'], 0.0)
        assert np.allclose(out, a, atol=1 / 255)

    def test_identity_params_is_identity(self):
        a = np.random.default_rng(1).random((16, 16, 3)).astype(np.float32)
        out = cg.grade_array(a, cg.IDENTITY, 1.0)
        assert np.allclose(out, a, atol=2 / 255)

    def test_exposure_brightens(self):
        a = np.full((8, 8, 3), 0.4, np.float32)
        out = cg.grade_array(a, dict(cg.IDENTITY, exposure=1.0))
        assert float(out.mean()) > float(a.mean()) + 0.1

    def test_contrast_increases_std(self):
        a = _grad()
        out = cg.grade_array(a, dict(cg.IDENTITY, contrast=1.4))
        assert out.std() > a.std() * 1.1

    def test_saturation_zero_makes_gray(self):
        a = np.zeros((8, 8, 3), np.float32)
        a[..., 0] = 0.8
        a[..., 1] = 0.3
        a[..., 2] = 0.1
        out = cg.grade_array(a, dict(cg.IDENTITY, saturation=0.0))
        assert np.allclose(out[..., 0], out[..., 1], atol=2 / 255)
        assert np.allclose(out[..., 0], out[..., 2], atol=2 / 255)

    def test_saturation_boosts_chroma(self):
        a = np.full((8, 8, 3), 0.45, np.float32)
        a[..., 0] += 0.1
        a[..., 2] -= 0.1
        out = cg.grade_array(a, dict(cg.IDENTITY, saturation=1.5))
        def chroma(x):
            return float((x.max(axis=2) - x.min(axis=2)).mean())
        assert chroma(out) > chroma(a)

    def test_black_point_keeps_white(self):
        a = np.full((8, 8, 3), 1.0, np.float32)
        a[:4] = 0.5
        out = cg.grade_array(a, dict(cg.IDENTITY, black=0.02))
        assert float(out[:4].mean()) < 0.5
        assert abs(float(out[4:].mean()) - 1.0) < 2 / 255

    def test_highlight_shoulder_pulls_down_highlights(self):
        a = np.full((8, 8, 3), 0.95, np.float32)
        out = cg.grade_array(a, dict(cg.IDENTITY, highlights=0.20))
        assert float(out.mean()) < 0.95 * 255 / 255

    def test_output_range_and_no_nan(self):
        for name, p in cg.BUILTIN_TEMPLATES.items():
            out = cg.grade_array(_grad(24, 24), p, 1.0)
            assert np.isfinite(out).all(), name
            assert out.min() >= 0.0 and out.max() <= 1.0, name

    def test_auto_black_clips_little(self):
        # 有暗部纹理的"照片"：auto 黑点只应切掉极少像素
        rng = np.random.default_rng(3)
        a = np.clip(0.02 + 0.7 * rng.random((64, 64, 3)).astype(np.float32) ** 2, 0, 1)
        out = cg.grade_array(a, dict(cg.IDENTITY, black_auto=True))
        assert float((out.max(axis=2) < 1 / 255).mean()) < 0.02


class TestTemplateIO:
    def test_parse_auto_and_clamp(self):
        p = cg.parse_template_section([('曝光', '9.0'), ('黑点', 'auto'), ('饱和', '-1')])
        assert p['black_auto'] is True and p['black'] == 0.0
        assert abs(p['exposure'] - 2.0) < 1e-9      # 上限夹紧
        assert p['saturation'] == 0.0               # 下限夹紧

    def test_parse_unknown_keys_ignored(self):
        p = cg.parse_template_section([('不存在', '1'), ('对比', '1.2')])
        assert abs(p['contrast'] - 1.2) < 1e-9

    def test_save_load_delete_roundtrip(self, tmp_path):
        cg.save_template(tmp_path, '我的', dict(cg.IDENTITY, contrast=1.2,
                                                saturation=0.8, black_auto=True))
        cp = configparser.ConfigParser(interpolation=None)
        cp.read(tmp_path / '用户设置.ini', encoding='utf-8')
        t = cg.load_templates(cp)
        assert '我的' in t
        assert abs(t['我的']['contrast'] - 1.2) < 1e-3
        assert abs(t['我的']['saturation'] - 0.8) < 1e-3
        assert t['我的']['black_auto'] is True

        cg.delete_template(tmp_path, '我的')
        cp2 = configparser.ConfigParser(interpolation=None)
        cp2.read(tmp_path / '用户设置.ini', encoding='utf-8')
        assert '我的' not in cg.load_templates(cp2)

    def test_reserved_off_name_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            cg.save_template(tmp_path, '原图', cg.IDENTITY)

    def test_template_names_hide_internal(self):
        names = cg.template_names({'原图': {}, '标准': {}, '__编辑中__': {}, '我的': {}})
        assert names[0] == '原图'
        assert '__编辑中__' not in names
        assert '标准' in names and '我的' in names

    def test_builtin_names(self):
        for n in ('标准', '视频感', '清新', '暖调', '黑白'):
            assert cg.is_builtin(n)


@pytest.fixture
def wm(monkeypatch):
    """把配置目录指到仓库内临时目录，避免污染真实 用户设置.ini"""
    import photo_watermark as pw
    tmp_path = Path(pw.__file__).parent / f"_test_grade_tmp_{os.getpid()}"
    tmp_path.mkdir(exist_ok=True)
    src_ini = Path(pw.__file__).parent / "水印设置.ini"
    if src_ini.exists():
        shutil.copy(src_ini, tmp_path / "水印设置.ini")
    monkeypatch.setattr(pw, "get_base_dir", lambda: tmp_path)
    yield pw, tmp_path
    shutil.rmtree(tmp_path, ignore_errors=True)


class TestPipeline:
    def test_grade_applies_to_photo_not_border(self, wm, monkeypatch):
        pw, tmp_path = wm
        from PIL import Image
        src = tmp_path / 'color.png'
        arr = np.zeros((600, 800, 3), np.uint8)
        arr[..., 0] = 200
        arr[..., 1] = 90
        arr[..., 2] = 40
        Image.fromarray(arr, 'RGB').save(src)

        monkeypatch.setattr(pw, 'SMART_STYLE', 'off')
        monkeypatch.setattr(pw, 'GRADE_TEMPLATE', '黑白')
        monkeypatch.setattr(pw, 'GRADE_STRENGTH', 100)
        out = tmp_path / 'out.jpg'
        assert pw.process_single_image(str(src), str(out), 'strip',
                                       explicit_style=True) == 'ok'
        with Image.open(out) as im:
            rgb = im.convert('RGB')
            photo_px = rgb.getpixel((rgb.width // 2, int(rgb.height * 0.2)))
            border_px = rgb.getpixel((rgb.width // 2, rgb.height - 3))
        # 照片本体：红绿蓝应一致（黑白模板）
        assert max(photo_px) - min(photo_px) <= 3, f'照片没去色: {photo_px}'
        # 白条边框：仍是白色，没被调色污染
        assert min(border_px) > 235, f'边框被染色: {border_px}'

    def test_off_template_keeps_original(self, wm, monkeypatch):
        pw, tmp_path = wm
        from PIL import Image
        src = tmp_path / 'color.png'
        arr = np.zeros((600, 800, 3), np.uint8)
        arr[..., 0] = 200
        arr[..., 1] = 90
        arr[..., 2] = 40
        Image.fromarray(arr, 'RGB').save(src)
        monkeypatch.setattr(pw, 'SMART_STYLE', 'off')
        monkeypatch.setattr(pw, 'GRADE_TEMPLATE', '原图')
        out = tmp_path / 'out.jpg'
        assert pw.process_single_image(str(src), str(out), 'strip',
                                       explicit_style=True) == 'ok'
        with Image.open(out) as im:
            rgb = im.convert('RGB')
            px = rgb.getpixel((rgb.width // 2, int(rgb.height * 0.2)))
        assert px[0] > px[1] > px[2], f'原图被改动: {px}'

    def test_unknown_template_falls_back(self, wm, monkeypatch):
        pw, tmp_path = wm
        from PIL import Image
        src = tmp_path / 'c.png'
        Image.new('RGB', (400, 300), (120, 120, 120)).save(src)
        monkeypatch.setattr(pw, 'SMART_STYLE', 'off')
        monkeypatch.setattr(pw, 'GRADE_TEMPLATE', '不存在的模板')
        out = tmp_path / 'o.jpg'
        assert pw.process_single_image(str(src), str(out), 'strip',
                                       explicit_style=True) == 'ok'
