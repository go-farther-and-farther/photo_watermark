# -*- coding: utf-8 -*-
"""压缩/转换功能测试：
1) compressor 纯函数单测（缩放/白底合成/目标体积/原子写/子文件夹清洗/sidecar）
2) process_single_image 的 none（无水印仅压缩）管线：输出子文件夹+原名、
   通用缩放、变大跳过、覆盖幂等、none+水印组合、strip 回归

图源选择说明：管线用例统一用「噪声图」（JPEG 远小于 PNG，压缩方向确定），
避免稀疏点阵图转 JPEG 反而变大、误触发「变大跳过」让断言失效。
"""
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import compressor


# ---------- 测试图工具 ----------

def noise_rgb(w, h):
    """随机噪声 RGB 图（难以压缩，适合测体积控制；JPEG 仍远小于无损 PNG）"""
    return Image.frombytes('RGB', (w, h), os.urandom(w * h * 3))


def noise_rgba_png(path, w, h):
    """不透明噪声 RGBA PNG（模拟游戏截图：RGBA 大 PNG → 小 JPG）"""
    rgb = noise_rgb(w, h)
    rgba = rgb.convert('RGBA')  # alpha 全 255
    rgba.save(path, 'PNG')
    return path


def noise_rgb_png(path, w, h):
    noise_rgb(w, h).save(path, 'PNG')
    return path


def flat_rgb_png(path, w=400, h=300):
    """纯色图 PNG（极小；转 JPEG 反而更大，用于触发「变大跳过」）"""
    Image.new('RGB', (w, h), (5, 5, 5)).save(path, 'PNG', optimize=True)
    return path


# ---------- resize_image ----------

def test_resize_zero_keeps_original():
    img = noise_rgb(400, 300)
    out = compressor.resize_image(img, 0)
    assert out is img
    assert out.size == (400, 300)


def test_resize_only_shrinks():
    img = noise_rgb(400, 300)
    assert compressor.resize_image(img, 7680).size == (400, 300)   # 档位更大：不放大
    assert compressor.resize_image(img, 400).size == (400, 300)    # 等于长边：不动
    assert compressor.resize_image(img, 100).size == (100, 75)     # 缩小：长边=档位


def test_resize_portrait_keeps_ratio():
    assert compressor.resize_image(noise_rgb(3000, 6000), 3000).size == (1500, 3000)


# ---------- flatten_to_rgb ----------

def test_flatten_rgba_transparent_becomes_white():
    out = compressor.flatten_to_rgb(Image.new('RGBA', (10, 10), (0, 0, 0, 0)))
    assert out.mode == 'RGB'
    assert out.getpixel((5, 5)) == (255, 255, 255)


def test_flatten_rgba_opaque_keeps_color():
    out = compressor.flatten_to_rgb(Image.new('RGBA', (10, 10), (10, 200, 30, 255)))
    assert out.getpixel((5, 5)) == (10, 200, 30)


def test_flatten_rgb_passthrough():
    img = Image.new('RGB', (10, 10), (1, 2, 3))
    assert compressor.flatten_to_rgb(img) is img


def test_flatten_palette_with_transparency():
    img = Image.new('P', (10, 10), 1)          # 全部指向调色板 1 号色（红）
    img.putpalette([255, 255, 255, 255, 0, 0])
    img.info['transparency'] = 1               # 1 号色透明
    out = compressor.flatten_to_rgb(img)
    assert out.mode == 'RGB'
    assert out.getpixel((5, 5)) == (255, 255, 255)


def test_flatten_grayscale():
    out = compressor.flatten_to_rgb(Image.new('L', (10, 10), 77))
    assert out.getpixel((5, 5)) == (77, 77, 77)


# ---------- encode_to_target_size ----------

def test_target_size_generous_limit_met_at_max_quality():
    data, q, met = compressor.encode_to_target_size(noise_rgb(400, 300), 1.0)
    assert met is True
    assert q == compressor.TARGET_QUALITY_MAX
    assert len(data) <= 1.0 * 1024 * 1024


def test_target_size_tight_limit_met_with_reduced_quality():
    img = noise_rgb(800, 600)
    full = compressor.encode_jpeg_bytes(img, compressor.TARGET_QUALITY_MAX)
    limit_mb = (len(full) * 0.6) / 1024 / 1024  # 比满质量小 40%
    data, q, met = compressor.encode_to_target_size(img, limit_mb)
    assert met is True
    assert q < compressor.TARGET_QUALITY_MAX
    assert len(data) <= limit_mb * 1024 * 1024


def test_target_size_impossible_falls_back_to_min_quality():
    data, q, met = compressor.encode_to_target_size(noise_rgb(1200, 900), 0.001)
    assert met is False
    assert q == compressor.TARGET_QUALITY_MIN
    assert len(data) > 0.001 * 1024 * 1024  # 确实超标（调用方负责提示）


# ---------- write_atomic ----------

def test_write_atomic_replaces_existing(tmp_path):
    target = tmp_path / 'out.jpg'
    target.write_bytes(b'old')
    compressor.write_atomic(b'new', target)
    assert target.read_bytes() == b'new'
    assert not list(tmp_path.glob('*.tmp'))


def test_write_atomic_failure_leaves_no_tmp(tmp_path):
    target = tmp_path / 'a_dir'  # 目标是目录：写入必失败
    target.mkdir()
    with pytest.raises(Exception):
        compressor.write_atomic(b'x', target)
    assert not list(tmp_path.glob('*.tmp'))  # finally 清理临时文件


# ---------- sanitize_subfolder ----------

@pytest.mark.parametrize('raw,expect', [
    ('JPEG', 'JPEG'),
    ('  jpeg ', 'jpeg'),
    ('my:folder?', 'myfolder'),
    ('a\\b/c*d', 'abcd'),
    ('', 'JPEG'),
    ('  ..//..  ', 'JPEG'),
])
def test_sanitize_subfolder(raw, expect):
    assert compressor.sanitize_subfolder(raw) == expect


# ---------- save_metadata_sidecar ----------

def test_sidecar_writes_parsed_workflow(tmp_path):
    info = {'workflow': '{"nodes": [1, 2]}', 'prompt': 'not-json-text'}
    p = compressor.save_metadata_sidecar(info, tmp_path, 'shot')
    assert p == tmp_path / 'shot.json'
    saved = json.loads(p.read_text(encoding='utf-8'))
    assert saved['workflow'] == {"nodes": [1, 2]}
    assert saved['prompt'] == 'not-json-text'


def test_sidecar_does_not_overwrite_existing(tmp_path):
    (tmp_path / 'shot.json').write_text('{"keep": 1}', encoding='utf-8')
    assert compressor.save_metadata_sidecar({'workflow': '{}'}, tmp_path, 'shot') is None
    assert json.loads((tmp_path / 'shot.json').read_text()) == {"keep": 1}


def test_sidecar_no_string_metadata(tmp_path):
    assert compressor.save_metadata_sidecar({'icc_profile': b'\x00'}, tmp_path, 'x') is None
    assert not list(tmp_path.glob('*.json'))


# ---------- process_single_image 管线（none 样式） ----------

@pytest.fixture
def wm(monkeypatch):
    """导入 photo_watermark 并隔离配置目录（沿用 test_bugfix_regressions 的做法：
    某些受限环境下系统临时目录不可写，用项目内临时目录）"""
    import photo_watermark as pw
    tmp_path = Path(pw.__file__).parent / f"_test_tmp_{os.getpid()}"
    tmp_path.mkdir(exist_ok=True)
    src_ini = Path(pw.__file__).parent / "水印设置.ini"
    if src_ini.exists():
        shutil.copy(src_ini, tmp_path / "水印设置.ini")
    monkeypatch.setattr(pw, "get_base_dir", lambda: tmp_path)
    yield pw, tmp_path
    shutil.rmtree(tmp_path, ignore_errors=True)


def test_none_outputs_to_subfolder_with_original_name(wm):
    pw, tmp_path = wm
    src = noise_rgba_png(tmp_path / 'shot.png', 400, 300)
    dummy_out = tmp_path / 'ignored_output.png'  # none 分支应无视调用方给的路径
    assert pw.process_single_image(str(src), str(dummy_out), 'none', '', explicit_style=True) == 'ok'
    out = tmp_path / 'JPEG' / 'shot.jpg'
    assert out.exists()
    assert not dummy_out.exists()
    assert src.exists()                                     # 源图不动
    assert Image.open(out).mode == 'RGB'                    # 透明通道已白底合成
    assert out.stat().st_size < src.stat().st_size          # 噪声 PNG → JPG 必然更小


def test_none_rerun_overwrites_no_duplicates(wm):
    pw, tmp_path = wm
    src = noise_rgba_png(tmp_path / 'shot.png', 400, 300)
    for _ in range(2):
        assert pw.process_single_image(str(src), str(tmp_path / 'x.png'), 'none', '', explicit_style=True) == 'ok'
    assert list((tmp_path / 'JPEG').glob('*.jpg')) == [tmp_path / 'JPEG' / 'shot.jpg']


def test_none_resize_via_global(wm, monkeypatch):
    pw, tmp_path = wm
    monkeypatch.setattr(pw, 'COMPRESS_MAX_EDGE', 200)
    src = noise_rgba_png(tmp_path / 'big.png', 400, 300)
    assert pw.process_single_image(str(src), str(tmp_path / 'x.png'), 'none', '', explicit_style=True) == 'ok'
    assert Image.open(tmp_path / 'JPEG' / 'big.jpg').size == (200, 150)


def test_none_target_size_via_global(wm, monkeypatch):
    pw, tmp_path = wm
    src = tmp_path / 't.jpg'
    img = noise_rgb(800, 600)
    img.save(src, 'JPEG', quality=95)
    src_size = src.stat().st_size
    # 目标 = 满质量体积的 60%：必然要降质量才能达到
    monkeypatch.setattr(pw, 'COMPRESS_TARGET_MB', src_size * 0.6 / 1024 / 1024)
    assert pw.process_single_image(str(src), str(tmp_path / 'x.jpg'), 'none', '', explicit_style=True) == 'ok'
    data = (tmp_path / 'JPEG' / 't.jpg').read_bytes()
    assert len(data) <= src_size * 0.6      # 自动降质量达到目标
    assert len(data) < src_size             # 未触发变大跳过


def test_none_skips_when_output_not_smaller(wm):
    pw, tmp_path = wm
    src = flat_rgb_png(tmp_path / 'flat.png')              # 纯色 PNG 极小，转 JPG 更大
    assert pw.process_single_image(str(src), str(tmp_path / 'x.png'), 'none', '', explicit_style=True) == 'ok'
    assert not (tmp_path / 'JPEG' / 'flat.jpg').exists()   # 变大跳过


def test_none_custom_subfolder(wm, monkeypatch):
    pw, tmp_path = wm
    monkeypatch.setattr(pw, 'COMPRESS_SUBFOLDER', '输出:图?')
    src = noise_rgba_png(tmp_path / 's.png', 400, 300)
    assert pw.process_single_image(str(src), str(tmp_path / 'x.png'), 'none', '', explicit_style=True) == 'ok'
    assert (tmp_path / '输出图' / 's.jpg').exists()         # 非法字符被清洗


def test_none_plus_strip_both_output(wm):
    pw, tmp_path = wm
    src = noise_rgb_png(tmp_path / 'combo.png', 400, 300)
    style_out = tmp_path / 'combo_strip_watermark.png'    # 多样式时文件名带实际样式名
    assert pw.process_single_image(str(src), str(style_out), 'none,strip', '', explicit_style=True) == 'ok'
    assert (tmp_path / 'JPEG' / 'combo.jpg').exists()      # none → 子文件夹原名
    assert style_out.exists()                              # strip → 样式命名输出
    assert Image.open(style_out).size[1] > 300             # 有白条边框


def test_strip_regression_unchanged(wm):
    """水印主路径回归：strip 仍走原逻辑，不经过压缩分支"""
    pw, tmp_path = wm
    src = flat_rgb_png(tmp_path / 'reg.png')
    out = tmp_path / 'reg_out.png'
    assert pw.process_single_image(str(src), str(out), 'strip', '', explicit_style=True) == 'ok'
    assert Image.open(out).size[1] > 300                   # 有白条边框
    assert not (tmp_path / 'JPEG').exists()                # 没误建压缩子文件夹
