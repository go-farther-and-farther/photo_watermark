# -*- coding: utf-8 -*-
"""
EXIF 读取核心模块（与水印工具解耦，可独立单测）

引擎选择优先级：
    1. 显式传入的 exiftool 路径
    2. 自动查找（内置打包 → 环境变量 EXIFTOOL_PATH → 同目录 → PATH → 常见安装位置）
    3. 都没有 → exifread 精简解析（Nikon 多数机身仍可读到快门数）

对外接口：
    find_exiftool() -> str | None
    read_exif_file(path, exiftool=None) -> dict
    read_exif_batch(paths, exiftool=None, progress=None) -> list[dict]

结果字典字段：
    path, ok, engine('exiftool'|'exifread'), error,
    model, shutter_count(int|None), lens, exposure, aperture, iso(int|None),
    focal, datetime
"""
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# 常用 EXIF 字段（ExifTool 参数顺序即输出顺序，可读性友好）
_EXIFTOOL_FIELDS = [
    '-Model', '-ShutterCount', '-LensModel', '-LensID',
    '-ExposureTime', '-FNumber', '-ISO', '-FocalLength', '-CreateDate',
]

# ExifTool 常见安装位置（Windows）
_EXIFTOOL_LOCATIONS = [
    ('LOCALAPPDATA', r'Programs\ExifTool\ExifTool.exe'),
    ('ProgramFiles', r'ExifTool\exiftool.exe'),
    ('ProgramFiles(x86)', r'ExifTool\exiftool.exe'),
]


def find_exiftool() -> str:
    """按优先级查找 ExifTool 可执行文件，找不到返回 None。"""
    candidates = []

    # 1. 环境变量显式指定
    env = os.environ.get('EXIFTOOL_PATH')
    if env:
        candidates.append(Path(env))

    # 2. PyInstaller 打包内置（解压到 _MEIPASS）
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        candidates.append(Path(meipass) / 'exiftool.exe')
        candidates.append(Path(meipass) / 'exiftool' / 'exiftool.exe')

    # 3. exe / 脚本同目录（含 exiftool/ 子目录）
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent
    candidates.append(base / 'exiftool.exe')
    candidates.append(base / 'exiftool' / 'exiftool.exe')

    # 4. PATH
    which = shutil.which('exiftool')
    if which:
        candidates.append(Path(which))

    # 5. Windows 常见安装位置
    for var, rel in _EXIFTOOL_LOCATIONS:
        root = os.environ.get(var)
        if root:
            candidates.append(Path(root) / rel)

    seen = set()
    for c in candidates:
        try:
            key = str(c).lower()
            if key in seen:
                continue
            seen.add(key)
            if c.is_file():
                return str(c)
        except OSError:
            continue
    return None


def _new_result(path, engine):
    return {
        'path': str(path),
        'ok': False,
        'engine': engine,
        'model': '',
        'shutter_count': None,
        'lens': '',
        'exposure': '',
        'aperture': '',
        'iso': None,
        'focal': '',
        'datetime': '',
        'error': '',
    }


# ---------- 字段格式化 ----------

def _fmt_aperture(val):
    """FNumber -> 'F4' / 'F5.6'；支持 exiftool 数字与 exifread 的 '28/5' 字符串。"""
    if val is None or val == '':
        return ''
    s = str(val).strip()
    try:
        if '/' in s:
            num, den = s.split('/')
            f = int(num) / int(den)
        else:
            f = float(s)
    except (ValueError, ZeroDivisionError, TypeError):
        return s
    if abs(f - round(f)) < 1e-9:
        return f'F{int(round(f))}'
    return f'F{f:.1f}'


def _fmt_exposure(val):
    """ExposureTime -> '1/8000s' / '0.5s' / '30s'。"""
    if val is None or val == '':
        return ''
    s = str(val).strip()
    if '/' in s:
        try:
            num, den = s.split('/')
            n, d = int(num), int(den)
        except (ValueError, ZeroDivisionError):
            return s
        if d <= 0:
            return s
        if n == 1:
            return f'1/{d}s'
        g = math.gcd(n, d)
        n, d = n // g, d // g
        if d == 1:
            return f'{n}s'
        return f'{n}/{d}s'
    try:
        f = float(s)
    except ValueError:
        return s
    if f >= 1:
        return f'{f:g}s'
    return f'{f:g}s'


def _fmt_focal(val):
    """FocalLength -> '33mm' / '120mm'；容忍 '33.0 mm' / '33' / 33.0。"""
    if val is None or val == '':
        return ''
    s = str(val).strip().lower().replace('mm', '').strip()
    try:
        f = float(s)
    except ValueError:
        return str(val).strip()
    if abs(f - round(f)) < 1e-9:
        return f'{int(round(f))}mm'
    return f'{f:g}mm'


def _fmt_datetime(val):
    s = str(val).strip()
    # '2026:08:16 13:25:18' -> '2026-08-16 13:25:18'（更接近日常习惯）
    return re.sub(r'^(\d{4}):(\d{2}):(\d{2})', r'\1-\2-\3', s)


def _clean_model(make, model):
    """品牌+型号精简显示：'NIKON CORPORATION NIKON Z 7_2' -> 'NIKON Z 7_2'。"""
    make = (make or '').strip().upper()
    model = (model or '').strip()
    if not model:
        return make
    for word in ('CORPORATION', 'CORP.', 'INC.', 'LTD.'):
        make = make.replace(word, '')
    make = make.strip()
    if make and model.upper().startswith(make):
        return model
    if make:
        return f'{make} {model}'
    return model


def _looks_like_image(path) -> bool:
    """用文件头魔数判断是否属于常见图片格式（ExifTool 无标签时的兜底判断）。"""
    try:
        with open(path, 'rb') as f:
            head = f.read(16)
    except OSError:
        return False
    if head[:3] == b'\xff\xd8\xff':          # JPEG
        return True
    if head.startswith(b'\x89PNG\r\n\x1a\n'):  # PNG
        return True
    if head.startswith(b'GIF8'):             # GIF
        return True
    if head.startswith(b'BM'):               # BMP
        return True
    if head[:4] in (b'II*\x00', b'MM\x00*'):  # TIFF 系（TIFF/NEF/DNG/ARW/CR2/ORF/RW2…）
        return True
    if head[4:8] == b'ftyp':                 # HEIF/HEIC/CR3
        return True
    return False


# ---------- ExifTool 引擎 ----------

def _read_with_exiftool(path, exe):
    result = _new_result(path, 'exiftool')
    p = Path(path)
    if not p.is_file():
        result['error'] = '文件不存在或不是文件'
        return result
    try:
        cmd = [exe, '-j', *_EXIFTOOL_FIELDS, '--', str(p)]
        proc = subprocess.run(
            cmd, capture_output=True, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
    except FileNotFoundError:
        result['error'] = '未找到 ExifTool 可执行文件'
        return result
    except subprocess.TimeoutExpired:
        result['error'] = '读取超时'
        return result

    if proc.returncode != 0 and not proc.stdout.strip():
        err = proc.stderr.decode('utf-8', errors='replace').strip()
        result['error'] = err.splitlines()[0] if err else f'ExifTool 退出码 {proc.returncode}'
        return result

    try:
        data = json.loads(proc.stdout.decode('utf-8', errors='replace'))
    except json.JSONDecodeError:
        result['error'] = 'ExifTool 输出解析失败'
        return result
    entry = data[0] if isinstance(data, list) else data

    stderr = proc.stderr.decode('utf-8', errors='replace').strip()
    has_tags = any(k in entry for k in
                   ('Model', 'ShutterCount', 'LensModel', 'LensID', 'ExposureTime',
                    'FNumber', 'ISO', 'FocalLength', 'CreateDate'))
    if not has_tags:
        # 合法图片但确实没有 EXIF（如截图/纯导出 JPEG）→ 成功但字段为空
        if _looks_like_image(path):
            result['ok'] = True
            return result
        result['error'] = '无法解析的图片文件（格式不支持或无 EXIF 信息）'
        return result

    make = str(entry.get('Make', ''))
    result['model'] = _clean_model(make, str(entry.get('Model', '')))
    sc = entry.get('ShutterCount')
    if sc is not None and sc != '':
        try:
            result['shutter_count'] = int(sc)
        except (ValueError, TypeError):
            pass
    result['lens'] = str(entry.get('LensModel') or entry.get('LensID') or '').strip()
    result['exposure'] = _fmt_exposure(entry.get('ExposureTime'))
    result['aperture'] = _fmt_aperture(entry.get('FNumber'))
    iso = entry.get('ISO')
    if iso is not None and iso != '':
        try:
            result['iso'] = int(iso)
        except (ValueError, TypeError):
            result['iso'] = iso
    result['focal'] = _fmt_focal(entry.get('FocalLength'))
    result['datetime'] = _fmt_datetime(entry.get('CreateDate', ''))
    result['ok'] = True
    return result


# ---------- exifread 引擎（无 ExifTool 时回退） ----------

def _read_with_exifread(path):
    result = _new_result(path, 'exifread')
    p = Path(path)
    if not p.is_file():
        result['error'] = '文件不存在或不是文件'
        return result
    try:
        import exifread
    except ImportError:
        result['error'] = '未安装 exifread 库，且未找到 ExifTool'
        return result

    try:
        with open(p, 'rb') as f:
            # 默认 details=True：否则 Nikon MakerNote（含快门数）会被跳过
            tags = exifread.process_file(f)
    except Exception as e:  # noqa: BLE001
        result['error'] = f'读取失败: {e}'
        return result
    if not tags:
        result['error'] = '无法解析 EXIF 信息'
        return result

    result['model'] = _clean_model(str(tags.get('Image Make', '')),
                                   str(tags.get('Image Model', '')))
    result['lens'] = str(tags.get('EXIF LensModel', '')).strip()

    # Nikon 快门数：TotalShutterReleases（含电子快门）优先，其次机械快门数
    for key in ('MakerNote TotalShutterReleases', 'MakerNote MechanicalShutterCount',
                'MakerNote ShutterCount'):
        if key in tags:
            try:
                result['shutter_count'] = int(str(tags[key]).strip())
            except ValueError:
                continue
            if result['shutter_count'] is not None:
                break

    result['exposure'] = _fmt_exposure(tags.get('EXIF ExposureTime'))
    result['aperture'] = _fmt_aperture(tags.get('EXIF FNumber'))
    iso = tags.get('EXIF ISOSpeedRatings')
    if iso is not None:
        try:
            result['iso'] = int(str(iso).strip())
        except ValueError:
            result['iso'] = str(iso).strip()
    result['focal'] = _fmt_focal(tags.get('EXIF FocalLength'))
    result['datetime'] = _fmt_datetime(tags.get('EXIF DateTimeOriginal', ''))
    result['ok'] = True
    return result


# ---------- 对外接口 ----------

def read_exif_file(path, exiftool=None):
    """读取单个文件的 EXIF。exiftool 为 None 时自动查找；找不到则用 exifread。"""
    exe = exiftool if exiftool is not None else find_exiftool()
    if exe:
        return _read_with_exiftool(str(path), exe)
    return _read_with_exifread(str(path))


def read_exif_batch(paths, exiftool=None, progress=None):
    """批量读取，逐文件容错。progress(i, n) 在每读完一个文件后回调。"""
    exe = exiftool if exiftool is not None else find_exiftool()
    results = []
    total = len(paths)
    for i, p in enumerate(paths, 1):
        if exe:
            results.append(_read_with_exiftool(str(p), exe))
        else:
            results.append(_read_with_exifread(str(p)))
        if progress:
            try:
                progress(i, total)
            except Exception:
                pass
    return results
