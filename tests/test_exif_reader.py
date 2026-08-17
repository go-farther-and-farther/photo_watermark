# -*- coding: utf-8 -*-
"""exif_reader 核心解析逻辑单元测试"""
import os
from pathlib import Path

import pytest

from exif_reader import (
    find_exiftool,
    read_exif_file,
    read_exif_batch,
    _read_with_exiftool,
    _read_with_exifread,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
JPEG_PATH = PROJECT_ROOT / "input" / "demo_photo.jpg"
# NEF 样张取自本机照片库（快门数只存在于 RAW MakerNote，JPEG 无法覆盖该路径）；
# 文件缺失时相关用例自动 skip（见 needs_nef），不影响其余测试。
NEF_PATH = Path(r"D:\photo\raw\Z6_101\DSC_0299.NEF")   # NIKON Z 6 / 快门 2219 / F1.8 / 1/30s / ISO100 / 50mm

needs_nef = pytest.mark.skipif(not NEF_PATH.exists(), reason="缺少 NEF 测试样张")
needs_jpeg = pytest.mark.skipif(not JPEG_PATH.exists(), reason="缺少 JPEG 测试样张")


# ---------- find_exiftool ----------

def test_find_exiftool_finds_installed_exe():
    exe = find_exiftool()
    assert exe, "本机应能找到 ExifTool"
    assert Path(exe).is_file()
    assert 'exiftool' in Path(exe).name.lower()


# ---------- ExifTool 引擎 ----------

@needs_nef
def test_exiftool_reads_nef_full_fields():
    exe = find_exiftool()
    r = _read_with_exiftool(str(NEF_PATH), exe)
    assert r['ok'] is True
    assert r['engine'] == 'exiftool'
    assert r['error'] == ''
    assert 'Z 6' in r['model']
    assert r['shutter_count'] == 2219
    assert r['aperture'] == 'F1.8'
    assert r['exposure'] == '1/30s'
    assert r['iso'] == 100
    assert r['focal'] == '50mm'
    assert 'NIKKOR Z 50mm f/1.8 S' in r['lens']
    assert r['datetime'].startswith('2024-04-12')


@needs_jpeg
def test_exiftool_reads_jpeg_without_shutter_count():
    exe = find_exiftool()
    r = _read_with_exiftool(str(JPEG_PATH), exe)
    assert r['ok'] is True
    assert 'Z 7' in r['model']
    assert r['shutter_count'] is None
    assert r['aperture'] == 'F5.6'
    assert r['exposure'] == '1/200s'


# ---------- exifread 引擎（无 ExifTool 回退） ----------

@needs_nef
def test_exifread_reads_nef_including_shutter_count():
    r = _read_with_exifread(str(NEF_PATH))
    assert r['ok'] is True
    assert r['engine'] == 'exifread'
    assert r['error'] == ''
    assert 'Z 6' in r['model']
    assert r['shutter_count'] == 2219
    assert r['aperture'] == 'F1.8'
    assert r['exposure'] == '1/30s'
    assert r['iso'] == 100
    assert r['focal'] == '50mm'


@needs_jpeg
def test_exifread_reads_jpeg():
    r = _read_with_exifread(str(JPEG_PATH))
    assert r['ok'] is True
    assert 'Z 7' in r['model']
    assert r['aperture'] == 'F5.6'
    assert r['exposure'] == '1/200s'


# ---------- 自动选择引擎 ----------

@needs_nef
def test_read_exif_file_autodetect_uses_exiftool():
    r = read_exif_file(str(NEF_PATH))
    assert r['ok'] is True
    assert r['shutter_count'] == 2219
    assert r['engine'] in ('exiftool', 'exifread')


# ---------- 错误处理 ----------

def test_read_missing_file_returns_error():
    r = read_exif_file(str(Path('D:/') / 'no_such_file_xyz.nef'))
    assert r['ok'] is False
    assert r['error']


@needs_jpeg
def test_read_bad_file_returns_error():
    # 沙箱禁止创建/删除临时文件，用仓库里现成的非图片文件模拟坏文件
    r = read_exif_file(str(PROJECT_ROOT / 'README.md'))
    assert r['ok'] is False
    assert r['error']


def test_read_directory_returns_error():
    r = read_exif_file(str(PROJECT_ROOT))
    assert r['ok'] is False
    assert r['error']


# ---------- 批量 ----------

@needs_jpeg
def test_batch_mixed_inputs_and_progress():
    paths = [str(JPEG_PATH), str(PROJECT_ROOT / 'README.md')]
    calls = []
    results = read_exif_batch(paths, progress=lambda i, n: calls.append((i, n)))
    assert len(results) == len(paths)
    assert results[0]['ok'] is True
    assert results[-1]['ok'] is False
    assert results[-1]['error']
    assert calls == [(1, 2), (2, 2)]
