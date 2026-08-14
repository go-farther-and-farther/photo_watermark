#!/usr/bin/env python3
"""
相机照片水印边框生成器
支持多种边框样式、EXIF拍摄参数显示、自定义文字、批量处理

使用方法:
    # 单张图片处理
    python photo_watermark.py input.jpg -o output.jpg

    # 批量处理文件夹
    python photo_watermark.py ./input/ -o ./output/

    # 修改配置：编辑 config.py 文件
"""

import argparse
import sys
import os
import re
import configparser
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple
from PIL import Image, ImageDraw, ImageFont

try:
    import exifread
except ImportError:
    print("错误: 请安装 exifread 库 - pip install exifread")
    sys.exit(1)


# 控制台输出容错：避免中文环境下重定向输出时因编码崩溃（如 © 等字符）
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(errors='replace')
        sys.stderr.reconfigure(errors='replace')
    except Exception:
        pass


def get_base_dir() -> Path:
    """获取程序所在目录（兼容exe和源码运行）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包的 exe
        return Path(sys.executable).parent
    else:
        # 源码运行
        return Path(__file__).parent


def load_ini_config() -> configparser.ConfigParser:
    """
    加载配置：默认配置（水印设置.ini）+ 个人配置（用户设置.ini）

    个人配置后加载，覆盖默认配置；个人配置不存在时全部用默认值。
    这样更新程序时个人设置保留在 用户设置.ini 中，不会被新版默认配置覆盖。
    """
    config = configparser.ConfigParser()
    default_path = get_base_dir() / '水印设置.ini'
    user_path = get_base_dir() / '用户设置.ini'

    if default_path.exists():
        try:
            config.read(default_path, encoding='utf-8')
            print(f"[OK] 已加载默认配置: {default_path.name}")
        except Exception as e:
            print(f"[警告] 默认配置读取失败: {e}")

    if user_path.exists():
        try:
            config.read(user_path, encoding='utf-8')  # 后读覆盖先读
            print(f"[OK] 已加载个人配置: {user_path.name}")
        except Exception as e:
            print(f"[警告] 个人配置读取失败: {e}")

    return config


# 加载 .ini 配置
_ini_config = load_ini_config()


def get_config_value(section: str, key: str, fallback, value_type: str = 'str'):
    """从 .ini 配置获取值，支持类型转换"""
    if _ini_config.has_option(section, key):
        if value_type == 'int':
            return _ini_config.getint(section, key)
        elif value_type == 'float':
            return _ini_config.getfloat(section, key)
        elif value_type == 'bool':
            return _ini_config.get(section, key).lower() in ('是', 'yes', 'true', '1')
        else:
            return _ini_config.get(section, key)
    return fallback


def parse_color(color_str: str) -> Tuple[int, int, int]:
    """解析颜色字符串（支持中文）"""
    colors = {
        'black': (0, 0, 0), 'white': (255, 255, 255),
        'red': (255, 0, 0), 'green': (0, 128, 0), 'blue': (0, 0, 255),
        'gray': (128, 128, 128), 'grey': (128, 128, 128),
        '黑色': (0, 0, 0), '白色': (255, 255, 255),
        '红色': (255, 0, 0), '绿色': (0, 128, 0), '蓝色': (0, 0, 255),
        '灰色': (128, 128, 128),
    }
    color_str = color_str.lower().strip()
    if color_str in colors:
        return colors[color_str]
    try:
        parts = [int(x.strip()) for x in color_str.split(',')]
        if len(parts) == 3 and all(0 <= x <= 255 for x in parts):
            return tuple(parts)
    except (ValueError, AttributeError):
        pass
    return (0, 0, 0)


def parse_position(position_str: str) -> str:
    """解析位置字符串（支持中文）"""
    positions = {
        '左上': 'top-left', '右上': 'top-right',
        '左下': 'bottom-left', '右下': 'bottom-right',
        'top-left': 'top-left', 'top-right': 'top-right',
        'bottom-left': 'bottom-left', 'bottom-right': 'bottom-right',
    }
    return positions.get(position_str.strip(), 'bottom-right')


# 导入配置（优先 .ini，其次 config.py，最后默认值）
try:
    from config import *
except ImportError:
    pass

# 用 .ini 配置覆盖（如果存在）
DEFAULT_STYLE = get_config_value('基础设置', '默认样式', DEFAULT_STYLE if 'DEFAULT_STYLE' in dir() else 'strip')
DEFAULT_INPUT = get_config_value('基础设置', '默认输入路径', DEFAULT_INPUT if 'DEFAULT_INPUT' in dir() else '')
DEFAULT_OUTPUT = get_config_value('基础设置', '默认输出路径', DEFAULT_OUTPUT if 'DEFAULT_OUTPUT' in dir() else '')
JPEG_QUALITY = get_config_value('基础设置', 'JPEG质量', JPEG_QUALITY if 'JPEG_QUALITY' in dir() else 98, 'int')
JPEG_SUBSAMPLING = get_config_value('基础设置', 'JPEG色度采样', JPEG_SUBSAMPLING if 'JPEG_SUBSAMPLING' in dir() else 0, 'int')
AUTO_OPEN_OUTPUT = get_config_value('基础设置', '自动打开输出', AUTO_OPEN_OUTPUT if 'AUTO_OPEN_OUTPUT' in dir() else True, 'bool')
SHOW_CONSOLE_WINDOW = get_config_value('基础设置', '显示控制台窗口', SHOW_CONSOLE_WINDOW if 'SHOW_CONSOLE_WINDOW' in dir() else False, 'bool')
BORDER_BACKGROUND_IMAGE = get_config_value('基础设置', '边框背景图', BORDER_BACKGROUND_IMAGE if 'BORDER_BACKGROUND_IMAGE' in dir() else '')
BORDER_BACKGROUND_OPACITY = get_config_value('基础设置', '边框背景图透明度', BORDER_BACKGROUND_OPACITY if 'BORDER_BACKGROUND_OPACITY' in dir() else 128, 'int')
DEFAULT_BRAND = get_config_value('基础设置', '默认品牌', DEFAULT_BRAND if 'DEFAULT_BRAND' in dir() else '')
DEFAULT_TEXT = get_config_value('基础设置', '自定义文字', DEFAULT_TEXT if 'DEFAULT_TEXT' in dir() else '')

# 文字模板
TEMPLATE_LINE1 = get_config_value('文字模板', '第一行格式', '{相机}')
TEMPLATE_LINE2 = get_config_value('文字模板', '第二行格式', '{镜头}')
TEMPLATE_PARAMS = get_config_value('文字模板', '右侧参数格式', '{焦距} {光圈} {快门} {ISO}')
TEMPLATE_DATE = get_config_value('文字模板', '右侧日期格式', '{日期}')
TEMPLATE_TRANSPARENT = get_config_value('文字模板', '半透明格式', '{相机} | {焦距} {光圈} {快门} {ISO}')
TEMPLATE_BORDER = get_config_value('文字模板', '边框格式', '{焦距} {光圈} {快门} {ISO} | {日期}')
TEMPLATE_BLUR = get_config_value('文字模板', '模糊格式', '{焦距} {光圈} {快门} {ISO}')
TEMPLATE_BLUR_LINE2 = get_config_value('文字模板', '模糊第二行格式', '{日期}')

# 字体设置
FONT_PATH = get_config_value('字体设置', '字体路径', '')

# 颜色设置
COLOR_BORDER = parse_color(get_config_value('颜色设置', '白条背景', '白色'))
COLOR_CAMERA = parse_color(get_config_value('颜色设置', '白条相机颜色', '30,30,30'))
COLOR_LENS = parse_color(get_config_value('颜色设置', '白条镜头颜色', '120,120,120'))
COLOR_PARAMS = parse_color(get_config_value('颜色设置', '白条参数颜色', '30,30,30'))
COLOR_DATE = parse_color(get_config_value('颜色设置', '白条日期颜色', '100,100,100'))
BLUR_TEXT_COLOR = parse_color(get_config_value('颜色设置', '模糊文字颜色', '白色'))

# 白条边框垂直对齐
STRIP_VALIGN = get_config_value('白条边框', '垂直对齐', '中')

# 白条边框样式
BORDER_HEIGHT_RATIO = get_config_value('白条边框', '边框高度', BORDER_HEIGHT_RATIO if 'BORDER_HEIGHT_RATIO' in dir() else 0.08, 'float')
FONT_SIZE_RATIO = get_config_value('白条边框', '字体大小', FONT_SIZE_RATIO if 'FONT_SIZE_RATIO' in dir() else 3, 'int')
LEFT_MARGIN_RATIO = get_config_value('白条边框', '左侧边距', LEFT_MARGIN_RATIO if 'LEFT_MARGIN_RATIO' in dir() else 0.025, 'float')
RIGHT_MARGIN_RATIO = get_config_value('白条边框', '右侧边距', RIGHT_MARGIN_RATIO if 'RIGHT_MARGIN_RATIO' in dir() else 0.025, 'float')
LINE_SPACING = get_config_value('白条边框', '文字间距', LINE_SPACING if 'LINE_SPACING' in dir() else 6, 'int')

# 半透明水印样式
TRANSPARENT_POSITION = parse_position(get_config_value('半透明水印', '位置', TRANSPARENT_POSITION if 'TRANSPARENT_POSITION' in dir() else 'bottom-right'))
TRANSPARENT_OPACITY = get_config_value('半透明水印', '透明度', TRANSPARENT_OPACITY if 'TRANSPARENT_OPACITY' in dir() else 128, 'int')
TRANSPARENT_FONT_RATIO = get_config_value('半透明水印', '字体大小', TRANSPARENT_FONT_RATIO if 'TRANSPARENT_FONT_RATIO' in dir() else 0.03, 'float')

# 纯色边框样式
BORDER_FRAME_COLOR = parse_color(get_config_value('纯色边框', '边框颜色', '黑色'))
BORDER_TEXT_COLOR = parse_color(get_config_value('纯色边框', '文字颜色', '白色'))
BORDER_SIDE_RATIO = get_config_value('纯色边框', '边框宽度', BORDER_SIDE_RATIO if 'BORDER_SIDE_RATIO' in dir() else 0.04, 'float')
BORDER_BOTTOM_RATIO = get_config_value('纯色边框', '底部宽度', BORDER_BOTTOM_RATIO if 'BORDER_BOTTOM_RATIO' in dir() else 0.08, 'float')

# 模糊边框样式
BLUR_BORDER_RATIO = get_config_value('模糊边框', '边框宽度', BLUR_BORDER_RATIO if 'BLUR_BORDER_RATIO' in dir() else 0.06, 'float')
BLUR_INTENSITY = get_config_value('模糊边框', '模糊强度', BLUR_INTENSITY if 'BLUR_INTENSITY' in dir() else 15, 'int')
BLUR_CORNER_RADIUS = get_config_value('模糊边框', '圆角大小', BLUR_CORNER_RADIUS if 'BLUR_CORNER_RADIUS' in dir() else 0.03, 'float')
BLUR_TEXT_SHADOW = get_config_value('模糊边框', '文字阴影', BLUR_TEXT_SHADOW if 'BLUR_TEXT_SHADOW' in dir() else True, 'bool')

# 智能样式（'auto'=按照片方向自动选样式，'off'=固定用默认样式）
SMART_STYLE = 'auto' if get_config_value('智能样式', '启用智能样式', SMART_STYLE if 'SMART_STYLE' in dir() else False, 'bool') else 'off'
LANDSCAPE_STYLE = get_config_value('智能样式', '横版样式', LANDSCAPE_STYLE if 'LANDSCAPE_STYLE' in dir() else 'strip')
PORTRAIT_STYLE = get_config_value('智能样式', '竖版样式', PORTRAIT_STYLE if 'PORTRAIT_STYLE' in dir() else 'blur')
SQUARE_STYLE = get_config_value('智能样式', '方形样式', SQUARE_STYLE if 'SQUARE_STYLE' in dir() else 'transparent')

# 确保必要变量存在
if 'DEFAULT_LOGO' not in dir():
    DEFAULT_LOGO = ''
if 'DEFAULT_TEXT' not in dir():
    DEFAULT_TEXT = ''
if 'LOGO_PARAMS_SPACING_RATIO' not in dir():
    LOGO_PARAMS_SPACING_RATIO = 0.03
if 'LOGO_HEIGHT_RATIO' not in dir():
    LOGO_HEIGHT_RATIO = 0.7
if 'VERTICAL_OFFSET_RATIO' not in dir():
    VERTICAL_OFFSET_RATIO = 0.15
if 'COLOR_CAMERA' not in dir():
    COLOR_CAMERA = (30, 30, 30)
if 'COLOR_LENS' not in dir():
    COLOR_LENS = (120, 120, 120)
if 'COLOR_PARAMS' not in dir():
    COLOR_PARAMS = (30, 30, 30)
if 'COLOR_DATE' not in dir():
    COLOR_DATE = (100, 100, 100)
if 'COLOR_BORDER' not in dir():
    COLOR_BORDER = (255, 255, 255)
if 'TRANSPARENT_TEXT_COLOR' not in dir():
    TRANSPARENT_TEXT_COLOR = (255, 255, 255)
if 'TRANSPARENT_MARGIN_RATIO' not in dir():
    TRANSPARENT_MARGIN_RATIO = 0.02
if 'BLUR_TEXT_COLOR' not in dir():
    BLUR_TEXT_COLOR = (255, 255, 255)
if 'BLUR_BOTTOM_RATIO_MULTIPLIER' not in dir():
    BLUR_BOTTOM_RATIO_MULTIPLIER = 1.8
if 'BLUR_BRIGHTNESS_FACTOR' not in dir():
    BLUR_BRIGHTNESS_FACTOR = 0.85
if 'BLUR_DOWNSAMPLE_FACTOR' not in dir():
    BLUR_DOWNSAMPLE_FACTOR = 4
if 'OUTPUT_FILENAME_FORMAT' not in dir():
    OUTPUT_FILENAME_FORMAT = '{name}_{style}_watermark'
if 'OVERWRITE_EXISTING' not in dir():
    OVERWRITE_EXISTING = False
if 'AUTO_OPEN_OUTPUT' not in dir():
    AUTO_OPEN_OUTPUT = True
if 'SHOW_CONSOLE_WINDOW' not in dir():
    SHOW_CONSOLE_WINDOW = False
if 'BORDER_BACKGROUND_IMAGE' not in dir():
    BORDER_BACKGROUND_IMAGE = ''
if 'BORDER_BACKGROUND_OPACITY' not in dir():
    BORDER_BACKGROUND_OPACITY = 128
if 'DEFAULT_BRAND' not in dir():
    DEFAULT_BRAND = ''

# ========== 控制台窗口显示控制（仅exe模式生效） ==========
# 源码运行时始终显示控制台，方便调试；exe模式按配置隐藏
CONSOLE_HIDDEN = False
if getattr(sys, 'frozen', False) and not SHOW_CONSOLE_WINDOW:
    CONSOLE_HIDDEN = True
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # 0 = SW_HIDE
    except Exception:
        pass

# 支持的图片格式
SUPPORTED_FORMATS = {'.jpg', '.jpeg', '.png', '.tiff', '.bmp'}


def read_exif(image_path: str) -> Dict[str, str]:
    """
    读取照片的EXIF信息

    Args:
        image_path: 图片文件路径

    Returns:
        包含EXIF信息的字典
    """
    exif_data = {
        'camera': '',
        'lens': '',
        'aperture': '',
        'shutter': '',
        'iso': '',
        'focal_length': '',
        'datetime': '',
        'orientation': 1,  # 默认方向
        'brand': '',  # 品牌
    }

    try:
        with open(image_path, 'rb') as f:
            tags = exifread.process_file(f, details=False)

        # 方向信息
        if 'Image Orientation' in tags:
            orientation_str = str(tags['Image Orientation']).strip()
            # 尝试解析数字
            try:
                exif_data['orientation'] = int(orientation_str)
            except ValueError:
                # 解析文字描述
                orientation_map = {
                    'Horizontal (normal)': 1,
                    'Rotated 90 CCW': 8,  # 逆时针90度 = EXIF的8
                    'Rotated 180': 3,
                    'Rotated 90 CW': 6,   # 顺时针90度 = EXIF的6
                    'Mirrored': 2,
                    'Mirrored horizontally, rotated 90 CW': 5,
                    'Mirrored vertically': 4,
                    'Mirrored horizontally, rotated 90 CCW': 7,
                }
                exif_data['orientation'] = orientation_map.get(orientation_str, 1)
                print(f"  EXIF方向: {orientation_str} -> {exif_data['orientation']}")

        # 品牌信息（优先从 Image Make 读取）
        if 'Image Make' in tags:
            make = str(tags['Image Make']).strip()
            exif_data['brand'] = make.upper()
            print(f"  EXIF品牌: {make} -> {exif_data['brand']}")

        # 相机型号
        if 'Image Model' in tags:
            make = str(tags.get('Image Make', '')).strip()
            model = str(tags['Image Model']).strip()
            # 精简显示：只保留品牌核心词+型号
            if make and model:
                # 去掉冗余词（大小写不敏感）
                make_upper = make.upper()
                for word in ['CORPORATION', 'CORP.', 'INC.', 'LTD.']:
                    make_upper = make_upper.replace(word, '')
                make_clean = make_upper.strip()
                # 如果清理后的品牌词出现在型号开头，只用型号
                if model.upper().startswith(make_clean):
                    exif_data['camera'] = model
                else:
                    exif_data['camera'] = f"{make_clean} {model}" if make_clean else model
            else:
                exif_data['camera'] = model

        # 镜头型号
        if 'EXIF LensModel' in tags:
            exif_data['lens'] = str(tags['EXIF LensModel']).strip()

        # 光圈
        if 'EXIF FNumber' in tags:
            try:
                aperture = tags['EXIF FNumber']
                aperture_str = str(aperture)
                # exifread 返回的格式如 "28/5"
                if '/' in aperture_str:
                    num, den = aperture_str.split('/')
                    f_value = int(num) / int(den)
                    # 整数光圈不显示小数点
                    if f_value == int(f_value):
                        exif_data['aperture'] = f"F{int(f_value)}"
                    else:
                        exif_data['aperture'] = f"F{f_value:.1f}"
                else:
                    f_value = float(aperture_str)
                    if f_value == int(f_value):
                        exif_data['aperture'] = f"F{int(f_value)}"
                    else:
                        exif_data['aperture'] = f"F{f_value:.1f}"
            except (ValueError, ZeroDivisionError):
                exif_data['aperture'] = str(aperture)

        # 快门速度
        if 'EXIF ExposureTime' in tags:
            shutter = tags['EXIF ExposureTime']
            if hasattr(shutter, 'num') and hasattr(shutter, 'den'):
                if shutter.num == 1:
                    exif_data['shutter'] = f"1/{shutter.den}s"
                elif shutter.den == 1:
                    # 快门速度 >= 1秒，显示为整数秒
                    exif_data['shutter'] = f"{shutter.num}s"
                else:
                    exif_data['shutter'] = f"{shutter.num}/{shutter.den}s"
            else:
                exif_data['shutter'] = str(shutter)

        # ISO
        if 'EXIF ISOSpeedRatings' in tags:
            exif_data['iso'] = f"ISO{tags['EXIF ISOSpeedRatings']}"

        # 焦距
        if 'EXIF FocalLength' in tags:
            focal = tags['EXIF FocalLength']
            if hasattr(focal, 'num') and hasattr(focal, 'den'):
                focal_mm = focal.num / focal.den
                exif_data['focal_length'] = f"{focal_mm:.0f}mm"
            else:
                exif_data['focal_length'] = f"{focal}mm"

        # 拍摄时间 - 优先从EXIF读取，fallback到文件创建时间
        for tag in ['EXIF DateTimeOriginal', 'EXIF DateTimeDigitized', 'Image DateTime']:
            if tag in tags:
                exif_data['datetime'] = str(tags[tag]).strip()
                break
        if not exif_data['datetime']:
            ctime = os.path.getctime(image_path)
            exif_data['datetime'] = datetime.fromtimestamp(ctime).strftime('%Y:%m:%d %H:%M:%S')

        # 输出识别结果
        print(f"  相机: {exif_data.get('camera', '未识别')}")
        print(f"  镜头: {exif_data.get('lens', '未识别')}")
        print(f"  参数: {exif_data.get('aperture', '-')} {exif_data.get('shutter', '-')} {exif_data.get('iso', '-')} {exif_data.get('focal_length', '-')}")

        # 文件夹名（用于模板 {文件夹}）
        exif_data['folder'] = Path(image_path).parent.name

    except Exception as e:
        print(f"[警告] 未找到EXIF信息: {e}")
        print(f"   提示：手机照片可能需要先关闭'优化存储'功能")

    return exif_data


def format_template(template: str, exif_data: Dict[str, str]) -> str:
    """
    根据模板格式化EXIF数据

    Args:
        template: 模板字符串，如 "{相机} | {焦距} {光圈}"
        exif_data: EXIF数据字典

    Returns:
        格式化后的字符串
    """
    # 日期格式化：2026:07:26 -> 2026-07-26
    date_str = exif_data.get('datetime', '')
    if date_str:
        date_str = date_str.replace(':', '-', 2)[:10]

    replacements = {
        '{相机}': exif_data.get('camera', ''),
        '{镜头}': exif_data.get('lens', ''),
        '{焦距}': exif_data.get('focal_length', ''),
        '{光圈}': exif_data.get('aperture', ''),
        '{快门}': exif_data.get('shutter', ''),
        '{ISO}': exif_data.get('iso', ''),
        '{日期}': date_str,
        '{品牌}': exif_data.get('brand', ''),
        '{文件夹}': exif_data.get('folder', ''),   # 照片所在文件夹名
    }

    result = template
    for key, value in replacements.items():
        result = result.replace(key, value)

    # 清理多余空格
    result = ' '.join(result.split())

    # 如果只剩分隔符号（如 "|"），说明没有可显示的内容，返回空
    if not any(ch.isalnum() for ch in result):
        return ''

    return result


# 字体缓存
_font_cache = {}

def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """
    获取字体，优先使用系统字体

    Args:
        size: 字体大小
        bold: 是否使用粗体

    Returns:
        字体对象
    """
    # 检查缓存
    cache_key = (size, bold)
    if cache_key in _font_cache:
        return _font_cache[cache_key]

    # 字体路径列表
    font_paths = []

    # 如果配置了字体路径，优先使用
    if FONT_PATH and Path(FONT_PATH).exists():
        font_paths.append(FONT_PATH)

    # Windows系统字体路径（粗体用 msyhbd.ttc）
    font_paths.extend([
        'C:/Windows/Fonts/msyhbd.ttc' if bold else 'C:/Windows/Fonts/msyh.ttc',  # 微软雅黑
        'C:/Windows/Fonts/simhei.ttf',     # 黑体
        'C:/Windows/Fonts/simsun.ttc',     # 宋体
        'C:/Windows/Fonts/arial.ttf',      # Arial
    ])

    for font_path in font_paths:
        try:
            font = ImageFont.truetype(font_path, size)
            _font_cache[cache_key] = font
            return font
        except (IOError, OSError):
            continue

    # 如果都失败，使用默认字体
    default_font = ImageFont.load_default()
    _font_cache[cache_key] = default_font
    return default_font


def get_logo_by_brand(brand: str, logo_dir: str = '') -> str:
    """
    根据相机品牌自动选择对应的Logo

    Args:
        brand: 相机品牌（如 NIKON, CANON, SONY, FUJIFILM）
        logo_dir: Logo文件所在目录

    Returns:
        Logo文件路径
    """
    # 品牌到Logo文件的映射（短关键字放前面，避免先匹配长的失败）
    brand_logo_map = {
        'NIKON': 'nikon_logo.png',
        'CANON': 'canon_logo.png',
        'SONY': 'sony_logo.png',
        'FUJI': 'fuji_logo.png',
        'HASSELBLAD': 'hasselblad_logo.png',
        'OLYMPUS': 'olympus_logo.png',
        'OM DIGITAL': 'olympus_logo.png',  # OM System（前身奥林巴斯）
        'OM SYSTEM': 'olympus_logo.png',   # OM System
        'PENTAX': 'pentax_logo.png',
        'RICOH': 'ricoh_logo.png',           # 理光独立Logo
        'LEICA': 'leica_logo.png',           # 徕卡
        'PANASONIC': 'panasonic_logo.png',
        # 手机/无人机品牌（可自行放入对应 logo 文件）
        'XIAOMI': 'xiaomi_logo.png',
        'HUAWEI': 'huawei_logo.png',
        'HONOR': 'honor_logo.png',
        'APPLE': 'apple_logo.png',
        'IPHONE': 'apple_logo.png',
        'DJI': 'dji_logo.png',
        'SAMSUNG': 'samsung_logo.png',
        'GOOGLE': 'google_logo.png',
        'VIVO': 'vivo_logo.png',
        'OPPO': 'oppo_logo.png',
    }

    # 清理品牌名称
    brand_upper = brand.upper().strip()

    # Logo所在目录（默认为 logos/ 子目录）
    logo_base = Path(logo_dir) if logo_dir else get_base_dir() / 'logos'

    # 查找匹配的Logo
    print(f"  品牌匹配: brand_upper=[{brand_upper}], logo_base=[{logo_base}]")
    for key, logo_file in brand_logo_map.items():
        if key in brand_upper:
            logo_path = logo_base / logo_file
            print(f"  匹配到: key=[{key}], logo_file=[{logo_file}], exists={logo_path.exists()}")
            if logo_path.exists():
                return str(logo_path)
            else:
                print(f"  Logo文件不存在: {logo_path}")

    # 未匹配到品牌，返回空（不使用Logo）
    print(f"  未匹配到品牌Logo: {brand_upper}")
    return ''


def auto_rotate_image(image: Image.Image, orientation: int) -> Image.Image:
    """
    根据EXIF方向信息自动旋转图片

    Args:
        image: 原始图片
        orientation: EXIF方向值

    Returns:
        旋转后的图片
    """
    if orientation == 2:
        image = image.transpose(Image.FLIP_LEFT_RIGHT)
    elif orientation == 3:
        image = image.rotate(180, expand=True)
    elif orientation == 4:
        image = image.transpose(Image.FLIP_TOP_BOTTOM)
    elif orientation == 5:
        image = image.rotate(90, expand=True).transpose(Image.FLIP_LEFT_RIGHT)
    elif orientation == 6:
        image = image.rotate(270, expand=True)
    elif orientation == 7:
        image = image.rotate(270, expand=True).transpose(Image.FLIP_LEFT_RIGHT)
    elif orientation == 8:
        image = image.rotate(90, expand=True)
    return image


def create_frame_canvas(size: Tuple[int, int], base_color: Tuple[int, int, int]) -> Image.Image:
    """
    创建水印边框画布：
    - 未设置背景图：纯色画布
    - 设置了背景图：图片铺满画布（居中裁剪），并按配置的透明度与底色混合（半透明）

    Args:
        size: 画布尺寸 (宽, 高)
        base_color: 底色 (R, G, B)

    Returns:
        画布图片
    """
    canvas = Image.new('RGB', size, base_color)
    bg_path = BORDER_BACKGROUND_IMAGE
    if bg_path and Path(bg_path).exists():
        try:
            bg = Image.open(bg_path).convert('RGB')
            target_w, target_h = size
            # cover 缩放：等比放大铺满，超出部分居中裁剪
            scale = max(target_w / bg.width, target_h / bg.height)
            new_w = max(target_w, int(bg.width * scale))
            new_h = max(target_h, int(bg.height * scale))
            bg = bg.resize((new_w, new_h), Image.Resampling.LANCZOS)
            left = (new_w - target_w) // 2
            top = (new_h - target_h) // 2
            bg = bg.crop((left, top, left + target_w, top + target_h))
            # 按透明度与底色混合
            alpha = max(0, min(255, BORDER_BACKGROUND_OPACITY)) / 255.0
            canvas = Image.blend(canvas, bg, alpha)
            print(f"  边框背景图: {Path(bg_path).name} (透明度: {int(alpha * 255)})")
        except Exception as e:
            print(f"  [警告] 背景图加载失败: {e}，使用纯色背景")
    return canvas


def get_params_text(exif_data: Dict[str, str]) -> str:
    """
    提取拍摄参数文本（焦距 光圈 快门 ISO）

    Args:
        exif_data: EXIF数据字典

    Returns:
        参数文本，如 "120mm F5.6 1/125s ISO1100"
    """
    params = []
    for key in ['focal_length', 'aperture', 'shutter', 'iso']:
        if exif_data.get(key):
            params.append(exif_data[key])
    return ' '.join(params)


def format_exif_text(exif_data: Dict[str, str], show_all: bool = False) -> str:
    """
    格式化EXIF信息为显示文本

    Args:
        exif_data: EXIF数据字典
        show_all: 是否显示所有信息

    Returns:
        格式化后的文本
    """
    parts = []

    # 相机型号
    if exif_data.get('camera'):
        parts.append(exif_data['camera'])

    # 拍摄参数
    params_text = get_params_text(exif_data)
    if params_text:
        parts.append(params_text)

    # 拍摄时间
    if show_all and exif_data.get('datetime'):
        parts.append(exif_data['datetime'])

    return '  -  '.join(parts) if parts else ''


def apply_white_border(
    image: Image.Image,
    exif_data: Dict[str, str],
    custom_text: str = '',
    logo_path: str = '',
) -> Image.Image:
    """
    应用白底黑字条形边框样式（尼康/佳能风格）

    布局：
    - 左上：镜头型号
    - 左下：相机型号
    - 右侧：Logo + 拍摄参数 + 日期时间

    Args:
        image: 原始图片
        exif_data: EXIF数据
        custom_text: 自定义文字
        logo_path: logo图片路径（可选）

    Returns:
        添加边框后的图片
    """
    width, height = image.size
    # 边框高度按短边计算：竖版照片白条不会过粗（与横版比例一致）
    border_height = int(min(width, height) * BORDER_HEIGHT_RATIO)

    # ========== 使用全局布局参数 ==========
    left_margin = int(width * LEFT_MARGIN_RATIO)
    right_margin = int(width * RIGHT_MARGIN_RATIO)
    logo_params_spacing = int(width * LOGO_PARAMS_SPACING_RATIO)
    vertical_offset = int(border_height * VERTICAL_OFFSET_RATIO)

    # 字体设置
    font_size = max(16, border_height // FONT_SIZE_RATIO)
    font = get_font(font_size)
    font_bold = get_font(font_size + 2)

    # ========== 计算位置 ==========
    margin_top = height + vertical_offset
    right_x = width - right_margin

    # 创建新图片（原图 + 底部边框），底色支持自定义半透明背景图
    new_height = height + border_height
    new_image = create_frame_canvas((width, new_height), COLOR_BORDER)

    # 粘贴原图
    new_image.paste(image, (0, 0))

    # 绘制文字
    draw = ImageDraw.Draw(new_image)

    # ========== 左侧：使用模板格式化 ==========
    line1_text = format_template(TEMPLATE_LINE1, exif_data)
    line2_text = format_template(TEMPLATE_LINE2, exif_data)

    # 垂直对齐计算
    if STRIP_VALIGN == '上':
        text_y = height + 4
    elif STRIP_VALIGN == '下':
        text_y = height + border_height - font_size * 2 - LINE_SPACING - 4
    else:  # 中
        text_y = margin_top

    # 第一行（深色）
    if line1_text:
        draw.text(
            (left_margin, text_y),
            line1_text,
            fill=COLOR_CAMERA,
            font=font_bold,
        )

    # 第二行（灰色）
    if line2_text:
        draw.text(
            (left_margin, text_y + font_size + LINE_SPACING),
            line2_text,
            fill=COLOR_LENS,
            font=font,
        )

    # ========== 右侧：使用模板格式化 ==========
    params_text = format_template(TEMPLATE_PARAMS, exif_data)
    datetime_text = format_template(TEMPLATE_DATE, exif_data)

    # 绘制参数（第一行右侧，最前面）
    params_x = right_x  # 默认右对齐
    if params_text:
        bbox = draw.textbbox((0, 0), params_text, font=font_bold)
        text_width = bbox[2] - bbox[0]
        params_x = right_x - text_width
        draw.text(
            (params_x, margin_top),
            params_text,
            fill=COLOR_PARAMS,
            font=font_bold,
        )

    # 加载Logo（如果有）- 绘制Logo在参数左侧
    logo_width = 0
    if logo_path and Path(logo_path).exists():
        try:
            logo = Image.open(logo_path)
            print(f"  Logo文件: {Path(logo_path).name} (原始尺寸: {logo.width}x{logo.height})")

            # 调整logo大小
            logo_height = int(border_height * LOGO_HEIGHT_RATIO)
            logo_ratio = logo_height / logo.height
            logo_width = int(logo.width * logo_ratio)
            logo = logo.resize((logo_width, logo_height), Image.Resampling.LANCZOS)

            # Logo位置（参数左侧）
            logo_x = params_x - logo_width - logo_params_spacing
            logo_y = height + (border_height - logo_height) // 2

            print(f"  Logo缩放后: {logo_width}x{logo_height}, 位置: ({logo_x}, {logo_y})")

            # 确保logo不会超出左边界
            if logo_x < left_margin:
                print(f"  警告: Logo位置超出左边界，调整到左边距")
                logo_x = left_margin

            new_image.paste(logo, (logo_x, logo_y), logo if logo.mode == 'RGBA' else None)
        except Exception as e:
            print(f"  [警告] Logo加载失败: {e}")
            print(f"     提示：将使用文字水印替代")

    # 绘制日期时间 + 自定义文字（第二行右侧）
    if datetime_text:
        datetime_display = datetime_text.replace(':', '-', 2)[:10]
        date_line = f"{datetime_display}  {custom_text}" if custom_text else datetime_display
        bbox = draw.textbbox((0, 0), date_line, font=font)
        text_width = bbox[2] - bbox[0]
        date_x = right_x - text_width
        draw.text(
            (date_x, margin_top + font_size + LINE_SPACING),
            date_line,
            fill=COLOR_DATE,
            font=font,
        )
    elif custom_text:
        bbox = draw.textbbox((0, 0), custom_text, font=font)
        text_width = bbox[2] - bbox[0]
        draw.text(
            (right_x - text_width, margin_top + font_size + LINE_SPACING),
            custom_text,
            fill=COLOR_DATE,
            font=font,
        )

    return new_image


def apply_transparent_watermark(
    image: Image.Image,
    text: str,
    position: str = '',
    opacity: int = 0,
    font_ratio: float = 0.0,
    text_color: Tuple[int, int, int] = None,
    margin_ratio: float = 0.0,
) -> Image.Image:
    """
    应用半透明水印样式

    Args:
        image: 原始图片
        text: 水印文字
        position: 位置（top-left, top-right, bottom-left, bottom-right）
        opacity: 透明度（0-255）
        font_ratio: 字体大小比例（相对于图片宽度）
        text_color: 文字颜色 (R, G, B)，None则使用配置默认值
        margin_ratio: 边距比例（相对于图片宽度）

    Returns:
        添加水印后的图片
    """
    # 使用配置默认值
    if not position:
        position = TRANSPARENT_POSITION
    if opacity <= 0:
        opacity = TRANSPARENT_OPACITY
    if font_ratio <= 0:
        font_ratio = TRANSPARENT_FONT_RATIO
    if text_color is None:
        text_color = TRANSPARENT_TEXT_COLOR
    if margin_ratio <= 0:
        margin_ratio = TRANSPARENT_MARGIN_RATIO

    # 创建透明图层
    overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    width, height = image.size
    font_size = max(16, int(width * font_ratio))
    font = get_font(font_size)

    # 计算文字尺寸
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    # 计算位置（带边距）
    margin = int(width * margin_ratio)
    positions = {
        'top-left': (margin, margin),
        'top-right': (width - text_width - margin, margin),
        'bottom-left': (margin, height - text_height - margin),
        'bottom-right': (width - text_width - margin, height - text_height - margin),
    }
    x, y = positions.get(position, positions['bottom-right'])

    # 绘制阴影
    shadow_offset = max(1, font_size // 20)
    draw.text(
        (x + shadow_offset, y + shadow_offset),
        text,
        fill=(0, 0, 0, opacity // 2),
        font=font,
    )

    # 绘制文字
    r, g, b = text_color
    draw.text((x, y), text, fill=(r, g, b, opacity), font=font)

    # 合并图层
    if image.mode != 'RGBA':
        image = image.convert('RGBA')
    result = Image.alpha_composite(image, overlay)

    # 转回RGB
    return result.convert('RGB')


def apply_color_border(
    image: Image.Image,
    exif_data: Dict[str, str],
    custom_text: str = '',
    border_color: Tuple[int, int, int] = None,
    text_color: Tuple[int, int, int] = None,
    border_side_ratio: float = 0.0,
    border_bottom_ratio: float = 0.0,
) -> Image.Image:
    """
    应用纯色边框+文字样式

    Args:
        image: 原始图片
        exif_data: EXIF数据
        custom_text: 自定义文字
        border_color: 边框颜色，None则使用配置默认值
        text_color: 文字颜色，None则使用配置默认值
        border_side_ratio: 左右两侧边框宽度比例
        border_bottom_ratio: 底部边框宽度比例

    Returns:
        添加边框后的图片
    """
    # 使用配置默认值
    if border_color is None:
        border_color = BORDER_FRAME_COLOR
    if text_color is None:
        text_color = BORDER_TEXT_COLOR
    if border_side_ratio <= 0:
        border_side_ratio = BORDER_SIDE_RATIO
    if border_bottom_ratio <= 0:
        border_bottom_ratio = BORDER_BOTTOM_RATIO

    width, height = image.size
    border_size = int(width * border_side_ratio)
    bottom_border = int(width * border_bottom_ratio)

    # 计算新图片尺寸
    new_width = width + 2 * border_size
    new_height = height + border_size + bottom_border

    # 创建新图片（底色支持自定义半透明背景图）
    new_image = create_frame_canvas((new_width, new_height), border_color)

    # 粘贴原图
    new_image.paste(image, (border_size, border_size))

    # 绘制文字
    draw = ImageDraw.Draw(new_image)
    font_size = max(12, bottom_border // 3)
    font = get_font(font_size)

    # 组合显示文本（使用模板）
    display_text = format_template(TEMPLATE_BORDER, exif_data)
    if custom_text:
        display_text = f"{display_text} | {custom_text}" if display_text else custom_text

    if display_text:
        # 居中显示
        bbox = draw.textbbox((0, 0), display_text, font=font)
        text_width = bbox[2] - bbox[0]
        x = (new_width - text_width) // 2
        y = height + border_size + (bottom_border - font_size) // 2
        draw.text((x, y), display_text, fill=text_color, font=font)

    return new_image


def apply_blur_border(
    image: Image.Image,
    exif_data: Dict[str, str],
    custom_text: str = '',
    border_ratio: float = 0.0,
    blur_intensity: int = 0,
    text_color: Tuple[int, int, int] = None,
    text_shadow: bool = True,
) -> Image.Image:
    """
    应用模糊边框样式
    把原图放大做模糊背景，再把清晰原图叠上去

    Args:
        image: 原始图片
        exif_data: EXIF数据
        custom_text: 自定义文字
        border_ratio: 边框宽度比例（相对于图片宽度）
        blur_intensity: 模糊强度（像素）
        text_color: 文字颜色
        text_shadow: 是否添加文字阴影

    Returns:
        添加边框后的图片
    """
    from PIL import ImageFilter

    # 使用配置默认值
    if border_ratio <= 0:
        border_ratio = BLUR_BORDER_RATIO
    if blur_intensity <= 0:
        blur_intensity = BLUR_INTENSITY
    if text_color is None:
        text_color = BLUR_TEXT_COLOR

    width, height = image.size
    border_size = int(width * border_ratio)
    bottom_border = int(width * border_ratio * BLUR_BOTTOM_RATIO_MULTIPLIER)  # 底部更宽，放文字

    # 计算新图片尺寸
    new_width = width + 2 * border_size
    new_height = height + border_size + bottom_border

    # ========== 创建模糊背景 ==========

    # 1. 先把原图缩小到 1/4（像素变 1/16，模糊速度快 16 倍）
    small_w = width // BLUR_DOWNSAMPLE_FACTOR
    small_h = height // BLUR_DOWNSAMPLE_FACTOR
    small_image = image.resize((small_w, small_h), Image.Resampling.LANCZOS)

    # 2. 对小图做高斯模糊（半径可以大一点，因为图小）
    small_blur = small_image.filter(ImageFilter.GaussianBlur(radius=blur_intensity))

    # 3. 把模糊的小图放大到目标尺寸（放大本身就带模糊效果）
    bg_image = small_blur.resize((new_width, new_height), Image.Resampling.LANCZOS)

    # 4. 再轻微模糊一次，消除放大锯齿
    bg_image = bg_image.filter(ImageFilter.GaussianBlur(radius=8))

    # 5. 稍微降低亮度，让边框暗一点，文字更清晰
    from PIL import ImageEnhance
    bg_image = ImageEnhance.Brightness(bg_image).enhance(BLUR_BRIGHTNESS_FACTOR)

    # 6. 给原图加圆角
    corner_radius = int(width * BLUR_CORNER_RADIUS)  # 相对于图片宽度
    corner_mask = Image.new('L', (width, height), 0)
    mask_draw = ImageDraw.Draw(corner_mask)
    mask_draw.rounded_rectangle(
        [(0, 0), (width - 1, height - 1)],
        radius=corner_radius,
        fill=255
    )

    # 7. 把清晰原图贴到中间（带圆角）
    bg_image.paste(image, (border_size, border_size), corner_mask)

    new_image = bg_image

    # ========== 绘制文字 ==========
    draw = ImageDraw.Draw(new_image)
    font_size = max(12, bottom_border // 5)
    font = get_font(font_size)

    # 使用模板格式化文本
    line1_text = format_template(TEMPLATE_BLUR, exif_data)
    line2_text = format_template(TEMPLATE_BLUR_LINE2, exif_data)

    # 计算垂直起始位置
    text_block_height = font_size * 2 + 8
    y_start = height + border_size + (bottom_border - text_block_height) // 2

    # 绘制第一行（拍摄参数）
    if line1_text:
        bbox = draw.textbbox((0, 0), line1_text, font=font)
        text_width = bbox[2] - bbox[0]
        x = (new_width - text_width) // 2
        if text_shadow:
            shadow_offset = max(1, font_size // 15)
            draw.text((x + shadow_offset, y_start + shadow_offset), line1_text,
                      fill=(0, 0, 0, 180), font=font)
        draw.text((x, y_start), line1_text, fill=text_color, font=font)

    # 绘制第二行（日期）
    if line2_text:
        bbox = draw.textbbox((0, 0), line2_text, font=font)
        text_width = bbox[2] - bbox[0]
        x = (new_width - text_width) // 2
        y2 = y_start + font_size + 6
        if text_shadow:
            shadow_offset = max(1, font_size // 15)
            draw.text((x + shadow_offset, y2 + shadow_offset), line2_text,
                      fill=(0, 0, 0, 180), font=font)
        draw.text((x, y2), line2_text, fill=text_color, font=font)

    # 绘制自定义文字（如果有，放在第三行）
    if custom_text:
        custom_font = get_font(max(12, font_size - 2))
        bbox = draw.textbbox((0, 0), custom_text, font=custom_font)
        text_width = bbox[2] - bbox[0]
        x = (new_width - text_width) // 2
        y3 = y_start + (font_size + 6) * 2
        if text_shadow:
            shadow_offset = max(1, font_size // 15)
            draw.text((x + shadow_offset, y3 + shadow_offset), custom_text,
                      fill=(0, 0, 0, 180), font=custom_font)
        draw.text((x, y3), custom_text, fill=text_color, font=custom_font)

    return new_image


def apply_single_style(image, style, exif_data, custom_text, logo_path, **kwargs):
    """
    应用单个水印样式

    Args:
        image: PIL Image对象
        style: 样式名称
        exif_data: EXIF数据
        custom_text: 自定义文字
        logo_path: Logo路径
        **kwargs: 其他参数

    Returns:
        处理后的Image对象
    """
    if style == 'strip':
        return apply_white_border(
            image, exif_data, custom_text,
            logo_path=logo_path,
        )
    elif style == 'transparent':
        # 模板参数 + 自定义签名（签名追加在参数后面，不替换参数）
        text = format_template(TEMPLATE_TRANSPARENT, exif_data)
        if custom_text:
            text = f"{text} {custom_text}" if text else custom_text
        if not text:
            # 兜底：签名 → 相机 → 拍摄日期（日期始终存在）
            text = DEFAULT_TEXT or exif_data.get('camera') or ''
            if not text and exif_data.get('datetime'):
                text = exif_data['datetime'].replace(':', '-', 2)[:10]
            if not text:
                print("  [警告] 没有可显示的水印文字")
        return apply_transparent_watermark(
            image, text,
            position=kwargs.get('position', ''),
            opacity=kwargs.get('opacity', 0),
            font_ratio=kwargs.get('font_ratio', 0.0),
            text_color=kwargs.get('text_color', None),
            margin_ratio=kwargs.get('margin_ratio', 0.0),
        )
    elif style == 'border':
        return apply_color_border(
            image, exif_data, custom_text,
            border_color=kwargs.get('border_color', None),
            text_color=kwargs.get('text_color', None),
            border_side_ratio=kwargs.get('border_side_ratio', 0.0),
            border_bottom_ratio=kwargs.get('border_bottom_ratio', 0.0),
        )
    elif style == 'blur':
        return apply_blur_border(
            image, exif_data, custom_text,
            border_ratio=kwargs.get('border_ratio', 0.0),
            blur_intensity=kwargs.get('blur_intensity', 0),
            text_color=kwargs.get('text_color', None),
            text_shadow=kwargs.get('text_shadow', True),
        )
    else:
        print(f"  [警告] 未知样式: {style}")
        return image


def process_single_image(
    input_path: str,
    output_path: str,
    style: str = 'strip',
    custom_text: str = '',
    **kwargs,
) -> bool:
    """
    处理单张图片

    Args:
        input_path: 输入图片路径
        output_path: 输出图片路径
        style: 边框样式（strip, transparent, border, blur，可逗号分隔多选）
        custom_text: 自定义文字
        **kwargs: 其他参数

    Returns:
        'ok' - 处理成功；'skipped' - 输出文件已存在被跳过；'failed' - 处理失败
    """
    try:
        # 读取图片
        image = Image.open(input_path)

        # 检查图片尺寸是否过小
        if image.width < 200 or image.height < 200:
            print(f"  [警告] 图片尺寸过小，跳过: {image.width}x{image.height}")
            return 'failed'

        # 确保是RGB模式
        if image.mode in ('RGBA', 'P'):
            image = image.convert('RGB')

        # 读取EXIF信息
        exif_data = read_exif(input_path)

        # 自动旋转图片（根据EXIF方向信息）
        orientation = exif_data.get('orientation', 1)
        if orientation != 1:
            image = auto_rotate_image(image, orientation)
            print(f"  图片已自动旋转 (方向: {orientation})")

        # 自动选择Logo（根据相机品牌）
        logo_path = kwargs.get('logo_path', '')
        if logo_path and not Path(logo_path).exists():
            # logo_path 不是有效路径，尝试当作品牌名匹配
            logo_path = get_logo_by_brand(logo_path)
            if logo_path:
                print(f"  指定Logo: {Path(logo_path).name}")
            else:
                print("  未找到匹配的品牌Logo")
        elif not logo_path:
            # logo_path 为空，优先用手动指定品牌，否则用EXIF品牌自动匹配
            brand = DEFAULT_BRAND or exif_data.get('brand', '')
            if brand:
                logo_path = get_logo_by_brand(brand)
                if logo_path:
                    if DEFAULT_BRAND:
                        print(f"  手动指定Logo: {Path(logo_path).name} (品牌: {brand})")
                    else:
                        print(f"  自动选择Logo: {Path(logo_path).name} (品牌: {brand})")
                else:
                    print(f"  未找到品牌Logo: {brand}")

        # 解析样式列表（支持逗号分隔多选，分别输出）
        styles = [s.strip() for s in style.split(',') if s.strip()]

        # 智能样式：按照片方向自动选择（需在旋转后判断；多样式时不覆盖用户选择）
        force_name = False
        if SMART_STYLE == 'auto' and len(styles) == 1:
            ratio = image.width / image.height
            if ratio > 1.2:
                resolved = LANDSCAPE_STYLE
            elif ratio < 0.8:
                resolved = PORTRAIT_STYLE
            else:
                resolved = SQUARE_STYLE
            if resolved != styles[0]:
                print(f"  [智能样式] 照片方向({image.width}x{image.height}) 自动使用「{resolved}」样式")
                styles = [resolved]
                force_name = True  # 文件名用实际样式，避免名实不符

        # 从kwargs中移除logo_path，避免重复传递
        kwargs_clean = {k: v for k, v in kwargs.items() if k != 'logo_path'}

        # 为每个样式分别生成文件
        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)
        output_ext = Path(output_path).suffix

        processed_count = 0
        for i, single_style in enumerate(styles):
            # 为每个样式生成独立的文件名
            if len(styles) > 1 or force_name:
                # 多样式/智能样式切换时，文件名包含实际样式名
                style_output_name = OUTPUT_FILENAME_FORMAT.format(
                    name=Path(input_path).stem,
                    style=single_style,
                )
                style_output_path = output_dir / f"{style_output_name}{output_ext}"
                print(f"  应用样式 [{i+1}/{len(styles)}]: {single_style}")
            else:
                # 单样式时，使用原始输出路径
                style_output_path = output_path

            # 检查是否覆盖已存在文件
            if not OVERWRITE_EXISTING and Path(style_output_path).exists():
                print(f"    [跳过] 文件已存在: {Path(style_output_path).name}")
                continue

            processed_count += 1

            # 应用样式（每次都从原图开始，不叠加）
            result = apply_single_style(image, single_style, exif_data, custom_text, logo_path, **kwargs_clean)

            # 保存图片
            if output_ext.lower() in ('.jpg', '.jpeg'):
                result.save(str(style_output_path), 'JPEG',
                            quality=kwargs.get('quality', JPEG_QUALITY),
                            subsampling=kwargs.get('subsampling', JPEG_SUBSAMPLING))
            elif output_ext.lower() == '.png':
                result.save(str(style_output_path), 'PNG')
            else:
                result.save(str(style_output_path))

            if len(styles) > 1:
                print(f"    -> 保存到: {style_output_path.name}")

        if processed_count == 0:
            return 'skipped'
        return 'ok'

    except Image.DecompressionBombError:
        print(f"  [失败] 图片过大，内存不足: {input_path}")
        return 'failed'
    except Exception as e:
        print(f"  [失败] 处理失败: {e}")
        return 'failed'


def batch_process(
    input_dir: str,
    output_dir: str,
    style: str = 'strip',
    custom_text: str = '',
    **kwargs,
) -> Tuple[int, int, float]:
    """
    批量处理文件夹中的图片

    Args:
        input_dir: 输入文件夹路径
        output_dir: 输出文件夹路径
        style: 边框样式
        custom_text: 自定义文字
        **kwargs: 其他参数

    Returns:
        (成功数量, 跳过数量, 总数量, 用时秒数)
    """
    input_path = Path(input_dir)
    output_path = Path(output_dir)

    # 确保输出目录存在
    output_path.mkdir(parents=True, exist_ok=True)

    # 查找所有支持的图片文件
    image_files = []
    for ext in SUPPORTED_FORMATS:
        image_files.extend(input_path.glob(f'*{ext}'))
        image_files.extend(input_path.glob(f'*{ext.upper()}'))

    # 去重并排序
    image_files = sorted(set(image_files))

    if not image_files:
        print(f"[警告] 在 {input_dir} 中没有找到支持的图片文件")
        print(f"   支持格式: {', '.join(SUPPORTED_FORMATS)}")
        return 0, 0, 0, 0

    print(f"找到 {len(image_files)} 张图片")

    # 尝试导入进度条
    try:
        from tqdm import tqdm
        use_progress_bar = True
    except ImportError:
        use_progress_bar = False

    success_count = 0
    skipped_count = 0
    start_time = datetime.now()

    # 解析样式列表（移到循环外，避免重复解析）
    styles = [s.strip() for s in style.split(',') if s.strip()]

    # 使用进度条或普通循环
    if use_progress_bar:
        iterator = tqdm(image_files, desc="处理中", unit="张")
    else:
        iterator = image_files

    for i, img_file in enumerate(iterator, 1):
        # 为第一个样式生成输出路径（process_single_image会为多样式生成独立文件）
        first_style = styles[0] if styles else style
        output_name = OUTPUT_FILENAME_FORMAT.format(
            name=img_file.stem,
            style=first_style,
        )
        output_file = output_path / f"{output_name}{img_file.suffix}"

        if not use_progress_bar:
            print(f"[{i}/{len(image_files)}] 处理: {img_file.name}")

        status = process_single_image(
            str(img_file), str(output_file), style, custom_text, **kwargs
        )
        if status == 'ok':
            success_count += 1
        elif status == 'skipped':
            skipped_count += 1
        else:
            if not use_progress_bar:
                print(f"  -> 处理失败")

    # 计算用时
    elapsed_time = (datetime.now() - start_time).total_seconds()

    return success_count, skipped_count, len(image_files), elapsed_time


def get_smart_style(image_path: str) -> str:
    """
    根据照片方向智能选择水印样式（考虑EXIF旋转方向后的真实宽高）

    Args:
        image_path: 图片路径

    Returns:
        样式名称
    """
    try:
        from PIL import ImageOps
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img)  # 应用EXIF方向，得到真实宽高
            width, height = img.size
            ratio = width / height

            if ratio > 1.2:
                return LANDSCAPE_STYLE  # 横版
            elif ratio < 0.8:
                return PORTRAIT_STYLE   # 竖版
            else:
                return SQUARE_STYLE     # 方形
    except Exception:
        return DEFAULT_STYLE


def color_to_name(color: Tuple[int, int, int]) -> str:
    """颜色元组转名称（用于设置窗口显示）"""
    names = {
        '黑色': (0, 0, 0), '白色': (255, 255, 255), '灰色': (128, 128, 128),
        '红色': (255, 0, 0), '绿色': (0, 128, 0), '蓝色': (0, 0, 255),
    }
    color = tuple(color)
    for name, c in names.items():
        if color == c:
            return name
    return ','.join(map(str, color))


def update_ini_value(section: str, key: str, value_str: str) -> bool:
    """
    保存设置到个人配置文件（用户设置.ini）

    不修改默认配置（水印设置.ini），这样更新程序时个人设置不会丢失；
    用户设置.ini 不存在时自动创建。

    Args:
        section: 配置段（如 基础设置）
        key: 键名（如 自定义文字）
        value_str: 新值（字符串形式）

    Returns:
        是否保存成功
    """
    user_path = get_base_dir() / '用户设置.ini'
    try:
        cp = configparser.ConfigParser()
        if user_path.exists():
            cp.read(user_path, encoding='utf-8')
        if not cp.has_section(section):
            cp.add_section(section)
        cp.set(section, key, value_str)
        user_path.parent.mkdir(parents=True, exist_ok=True)
        with open(user_path, 'w', encoding='utf-8') as f:
            f.write('# ==========================================\n')
            f.write('# 个人设置（由程序设置窗口自动生成，无需手动编辑）\n')
            f.write('# 此文件不会被程序更新覆盖，请放心使用\n')
            f.write('# ==========================================\n')
            cp.write(f)
        return True
    except Exception as e:
        print(f"[警告] 保存个人配置失败: {e}")
        return False


def fmt_float(value: float) -> str:
    """浮点格式化为ini值：去掉多余的尾随零（0.080 -> 0.08, 1.000 -> 1）"""
    s = f"{value:.3f}".rstrip('0').rstrip('.')
    return s if s else '0'


def refresh_globals_from_ini():
    """设置窗口保存后，重新读取 ini 并更新程序内配置（立即生效）"""
    global _ini_config, DEFAULT_STYLE, DEFAULT_TEXT, JPEG_QUALITY, JPEG_SUBSAMPLING, AUTO_OPEN_OUTPUT, \
        SHOW_CONSOLE_WINDOW, BORDER_BACKGROUND_IMAGE, BORDER_BACKGROUND_OPACITY, \
        TRANSPARENT_POSITION, TRANSPARENT_OPACITY, TRANSPARENT_FONT_RATIO, \
        BORDER_FRAME_COLOR, BORDER_TEXT_COLOR, DEFAULT_BRAND, SMART_STYLE
    _ini_config = load_ini_config()
    DEFAULT_STYLE = get_config_value('基础设置', '默认样式', DEFAULT_STYLE)
    DEFAULT_TEXT = get_config_value('基础设置', '自定义文字', DEFAULT_TEXT)
    DEFAULT_BRAND = get_config_value('基础设置', '默认品牌', DEFAULT_BRAND)
    SMART_STYLE = 'auto' if get_config_value('智能样式', '启用智能样式', False, 'bool') else 'off'
    JPEG_QUALITY = get_config_value('基础设置', 'JPEG质量', JPEG_QUALITY, 'int')
    JPEG_SUBSAMPLING = get_config_value('基础设置', 'JPEG色度采样', JPEG_SUBSAMPLING, 'int')
    JPEG_QUALITY = get_config_value('基础设置', 'JPEG质量', JPEG_QUALITY, 'int')
    AUTO_OPEN_OUTPUT = get_config_value('基础设置', '自动打开输出', AUTO_OPEN_OUTPUT, 'bool')
    SHOW_CONSOLE_WINDOW = get_config_value('基础设置', '显示控制台窗口', SHOW_CONSOLE_WINDOW, 'bool')
    BORDER_BACKGROUND_IMAGE = get_config_value('基础设置', '边框背景图', BORDER_BACKGROUND_IMAGE)
    BORDER_BACKGROUND_OPACITY = get_config_value('基础设置', '边框背景图透明度', BORDER_BACKGROUND_OPACITY, 'int')
    TRANSPARENT_POSITION = parse_position(get_config_value('半透明水印', '位置', TRANSPARENT_POSITION))
    TRANSPARENT_OPACITY = get_config_value('半透明水印', '透明度', TRANSPARENT_OPACITY, 'int')
    TRANSPARENT_FONT_RATIO = get_config_value('半透明水印', '字体大小', TRANSPARENT_FONT_RATIO, 'float')
    BORDER_FRAME_COLOR = parse_color(get_config_value('颜色设置', '边框颜色', '黑色'))
    BORDER_TEXT_COLOR = parse_color(get_config_value('颜色设置', '边框文字颜色', '白色'))


def open_settings_window(parent=None) -> bool:
    """
    打开设置窗口，所有设置保存到 水印设置.ini 并立即生效

    Args:
        parent: 父窗口（设置窗口作为其子窗口）

    Returns:
        True=已保存，False=取消
    """
    import tkinter as tk
    from tkinter import ttk, filedialog, font as tkfont

    win = tk.Toplevel(parent) if parent else tk.Tk()
    win.title("Photo Watermark - 设置")
    win.resizable(False, False)
    if parent:
        win.transient(parent)
        win.grab_set()

    # 中文字体
    try:
        families = set(tkfont.families(win))
        for name in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "微软雅黑"):
            if name in families:
                tkfont.nametofont("TkDefaultFont").configure(family=name, size=9)
                break
    except Exception:
        pass

    saved = {'flag': False}

    # ===== 配置变量 =====
    _brand_code = {n: c for n, c in BRAND_OPTIONS}
    _brand_name = {c: n for n, c in BRAND_OPTIONS}
    v_brand = tk.StringVar(value=_brand_name.get(DEFAULT_BRAND.upper(), '自动'))
    v_text = tk.StringVar(value=DEFAULT_TEXT)
    v_style = tk.StringVar(value=DEFAULT_STYLE.split(',')[0])
    _sub_map = {0: '4:4:4(最清晰)', 1: '4:2:2', 2: '4:2:0(文件小)'}
    _sub_rev = {v: k for k, v in _sub_map.items()}
    v_sub_display = tk.StringVar(value=_sub_map.get(JPEG_SUBSAMPLING, _sub_map[0]))
    v_bg = tk.StringVar(value=BORDER_BACKGROUND_IMAGE)
    v_bg_alpha = tk.IntVar(value=BORDER_BACKGROUND_OPACITY)
    v_quality = tk.IntVar(value=JPEG_QUALITY)
    v_strip_h = tk.IntVar(value=int(BORDER_HEIGHT_RATIO * 1000))          # 千分比
    v_pos = tk.StringVar(value={'top-left': '左上', 'top-right': '右上',
                                'bottom-left': '左下', 'bottom-right': '右下'}
                         .get(TRANSPARENT_POSITION, '右下'))
    v_alpha = tk.IntVar(value=TRANSPARENT_OPACITY)
    v_font = tk.IntVar(value=int(TRANSPARENT_FONT_RATIO * 1000))          # 千分比
    v_border_color = tk.StringVar(value=color_to_name(BORDER_FRAME_COLOR))
    v_border_text = tk.StringVar(value=color_to_name(BORDER_TEXT_COLOR))
    v_auto = tk.BooleanVar(value=bool(AUTO_OPEN_OUTPUT))
    v_console = tk.BooleanVar(value=bool(SHOW_CONSOLE_WINDOW))
    v_smart = tk.BooleanVar(value=(SMART_STYLE == 'auto'))

    def add_scale(parent, row, label, var, frm, to, fmt, divisor=1):
        """一行：文字 + 滑杆 + 实时数值"""
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', pady=2)
        val_lb = ttk.Label(parent, text=fmt % (var.get() / divisor), width=6, anchor='e')
        val_lb.grid(row=row, column=2, sticky='e', padx=(4, 0))

        def _upd(v, vv=var, ll=val_lb, ff=fmt, d=divisor):
            ll.config(text=ff % (float(v) / d))

        ttk.Scale(parent, from_=frm, to=to, variable=var, length=170, command=_upd).grid(
            row=row, column=1, sticky='ew', pady=2)

    def browse_bg():
        path = filedialog.askopenfilename(
            title="选择边框背景图",
            filetypes=[("图片文件", "*.jpg *.jpeg *.png *.bmp *.tiff"), ("所有文件", "*.*")],
            parent=win,
        )
        if path:
            v_bg.set(path)

    def on_save():
        update_ini_value('基础设置', '自定义文字', v_text.get())
        update_ini_value('基础设置', '默认样式', v_style.get())
        update_ini_value('基础设置', '默认品牌', _brand_code.get(v_brand.get(), ''))
        update_ini_value('基础设置', '边框背景图', v_bg.get())
        update_ini_value('基础设置', '边框背景图透明度', str(v_bg_alpha.get()))
        update_ini_value('基础设置', 'JPEG质量', str(v_quality.get()))
        update_ini_value('基础设置', 'JPEG色度采样', str(_sub_rev.get(v_sub_display.get(), 0)))
        update_ini_value('基础设置', '自动打开输出', '是' if v_auto.get() else '否')
        update_ini_value('基础设置', '显示控制台窗口', '是' if v_console.get() else '否')
        update_ini_value('智能样式', '启用智能样式', '是' if v_smart.get() else '否')
        update_ini_value('白条边框', '边框高度', fmt_float(v_strip_h.get() / 1000))
        update_ini_value('半透明水印', '位置', v_pos.get())
        update_ini_value('半透明水印', '透明度', str(v_alpha.get()))
        update_ini_value('半透明水印', '字体大小', fmt_float(v_font.get() / 1000))
        update_ini_value('颜色设置', '边框颜色', v_border_color.get())
        update_ini_value('颜色设置', '边框文字颜色', v_border_text.get())
        refresh_globals_from_ini()
        saved['flag'] = True
        win.destroy()

    # ===== 布局 =====
    outer = ttk.Frame(win, padding=10)
    outer.pack(fill='both', expand=True)

    # 基础设置
    f1 = ttk.LabelFrame(outer, text="基础设置", padding=8)
    f1.grid(row=0, column=0, sticky='nsew', padx=(0, 5), pady=3)
    ttk.Label(f1, text="自定义文字/签名:").grid(row=0, column=0, sticky='w', pady=2)
    ttk.Entry(f1, textvariable=v_text, width=22).grid(row=0, column=1, columnspan=2, sticky='ew', pady=2)
    ttk.Label(f1, text="默认样式:").grid(row=1, column=0, sticky='w', pady=2)
    ttk.Combobox(f1, textvariable=v_style, values=['strip', 'transparent', 'border', 'blur'],
                 state='readonly', width=20).grid(row=1, column=1, columnspan=2, sticky='ew', pady=2)
    ttk.Label(f1, text="品牌(Logo):").grid(row=2, column=0, sticky='w', pady=2)
    ttk.Combobox(f1, textvariable=v_brand,
                 values=[n for n, _ in BRAND_OPTIONS], state='readonly', width=20).grid(
        row=2, column=1, columnspan=2, sticky='ew', pady=2)
    ttk.Label(f1, text="边框背景图:").grid(row=3, column=0, sticky='w', pady=2)
    ttk.Entry(f1, textvariable=v_bg, width=22).grid(row=3, column=1, sticky='ew', pady=2)
    ttk.Button(f1, text="浏览...", command=browse_bg).grid(row=3, column=2, padx=(4, 0), pady=2)
    add_scale(f1, 4, "背景图透明度:", v_bg_alpha, 0, 255, "%d", 1)
    add_scale(f1, 5, "JPEG质量:", v_quality, 50, 100, "%d", 1)

    # 半透明水印
    f2 = ttk.LabelFrame(outer, text="半透明水印", padding=8)
    f2.grid(row=0, column=1, sticky='nsew', padx=(5, 0), pady=3)
    ttk.Label(f2, text="位置:").grid(row=0, column=0, sticky='w', pady=2)
    ttk.Combobox(f2, textvariable=v_pos, values=['左上', '右上', '左下', '右下'],
                 state='readonly', width=18).grid(row=0, column=1, columnspan=2, sticky='ew', pady=2)
    add_scale(f2, 1, "透明度:", v_alpha, 0, 255, "%d", 1)
    add_scale(f2, 2, "字体大小:", v_font, 10, 60, "%.3f", 1000)

    # 白条边框
    f3 = ttk.LabelFrame(outer, text="白条边框（strip）", padding=8)
    f3.grid(row=1, column=0, sticky='nsew', padx=(0, 5), pady=3)
    add_scale(f3, 0, "边框高度:", v_strip_h, 40, 150, "%.3f", 1000)
    ttk.Label(f3, text="（0.05=窄 0.10=宽）", foreground='#808080').grid(
        row=1, column=0, columnspan=3, sticky='w', pady=(0, 2))

    # 颜色 / 输出
    f4 = ttk.LabelFrame(outer, text="颜色与输出", padding=8)
    f4.grid(row=1, column=1, sticky='nsew', padx=(5, 0), pady=3)
    ttk.Label(f4, text="边框颜色:").grid(row=0, column=0, sticky='w', pady=2)
    ttk.Combobox(f4, textvariable=v_border_color, values=['黑色', '白色', '灰色', '红色', '绿色', '蓝色'],
                 state='readonly', width=14).grid(row=0, column=1, columnspan=2, sticky='ew', pady=2)
    ttk.Label(f4, text="边框文字颜色:").grid(row=1, column=0, sticky='w', pady=2)
    ttk.Combobox(f4, textvariable=v_border_text, values=['黑色', '白色', '灰色', '红色', '绿色', '蓝色'],
                 state='readonly', width=14).grid(row=1, column=1, columnspan=2, sticky='ew', pady=2)
    ttk.Label(f4, text="JPEG采样:").grid(row=2, column=0, sticky='w', pady=2)
    ttk.Combobox(f4, textvariable=v_sub_display,
                 values=[_sub_map[0], _sub_map[1], _sub_map[2]],
                 state='readonly', width=14).grid(row=2, column=1, columnspan=2, sticky='ew', pady=2)
    ttk.Checkbutton(f4, text="处理完自动打开输出文件夹", variable=v_auto).grid(
        row=3, column=0, columnspan=3, sticky='w', pady=2)
    ttk.Checkbutton(f4, text="显示控制台窗口（exe）", variable=v_console).grid(
        row=4, column=0, columnspan=3, sticky='w', pady=2)
    ttk.Checkbutton(f4, text="智能样式（竖版自动用模糊边框）", variable=v_smart).grid(
        row=5, column=0, columnspan=3, sticky='w', pady=2)

    # 按钮
    btns = ttk.Frame(outer)
    btns.grid(row=2, column=0, columnspan=2, pady=(10, 0))
    ttk.Button(btns, text="保存", command=on_save, width=14).pack(side='left', padx=6)
    ttk.Button(btns, text="取消", command=win.destroy, width=14).pack(side='left', padx=6)

    # 窗口居中
    win.update_idletasks()
    w = win.winfo_reqwidth()
    h = win.winfo_reqheight()
    x = (win.winfo_screenwidth() - w) // 2
    y = (win.winfo_screenheight() - h) // 3
    win.geometry(f"{w}x{h}+{x}+{y}")

    # 模态等待：窗口销毁后返回（wait_window 处理事件直到本窗口关闭）
    win.wait_window()
    return saved['flag']


# 水印样式选项（GUI勾选框/样式选择窗口共用）
STYLE_OPTIONS = [
    ('strip', '白底条形', '照片下方参数条，尼康/佳能风格'),
    ('transparent', '半透明', '文字直接叠加在照片上'),
    ('border', '纯色边框', '边框包裹照片，底部显示参数'),
    ('blur', '模糊边框', '边缘模糊背景，效果自然'),
]

# 品牌选项（显示名, 配置值）；空值=自动识别EXIF品牌
# 用于设置窗口（全局默认）和选择窗口（本次处理强制指定）
BRAND_OPTIONS = [
    ('自动', ''), ('尼康', 'NIKON'), ('佳能', 'CANON'), ('索尼', 'SONY'),
    ('富士', 'FUJI'), ('哈苏', 'HASSELBLAD'), ('徕卡', 'LEICA'), ('奥林巴斯', 'OLYMPUS'),
    ('宾得', 'PENTAX'), ('理光', 'RICOH'), ('松下', 'PANASONIC'), ('小米', 'XIAOMI'),
    ('华为', 'HUAWEI'), ('荣耀', 'HONOR'), ('苹果', 'APPLE'), ('大疆', 'DJI'),
    ('三星', 'SAMSUNG'), ('谷歌', 'GOOGLE'), ('vivo', 'VIVO'), ('OPPO', 'OPPO'),
]


def choose_style_gui(default_style: str = '') -> str:
    """
    弹出水印样式选择窗口（拖拽照片到exe后，跳过选图直接选择样式）

    Args:
        default_style: 默认勾选的样式（逗号分隔多选）

    Returns:
        (样式字符串, 品牌代码)；用户取消返回 ('', '')
    """
    import tkinter as tk
    from tkinter import ttk, font as tkfont

    root = tk.Tk()
    root.title("Photo Watermark - 选择水印样式")
    root.attributes('-topmost', True)
    root.resizable(False, False)

    # 中文字体
    try:
        families = set(tkfont.families(root))
        for name in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "微软雅黑"):
            if name in families:
                tkfont.nametofont("TkDefaultFont").configure(family=name, size=10)
                break
    except Exception:
        pass

    base_font = tkfont.nametofont("TkDefaultFont")
    small_font = base_font.copy()
    small_font.configure(size=9)

    default_styles = [s.strip() for s in (default_style or DEFAULT_STYLE).split(',') if s.strip()] or ['strip']
    style_vars = {}
    for key, _, _ in STYLE_OPTIONS:
        style_vars[key] = tk.BooleanVar(value=(key in default_styles))

    _brand_code = {n: c for n, c in BRAND_OPTIONS}
    brand_var = tk.StringVar(value='自动')

    result = {'style': '', 'brand': ''}

    def confirm():
        checked = [k for k, _, _ in STYLE_OPTIONS if style_vars[k].get()]
        result['style'] = ','.join(checked) if checked else 'strip'
        result['brand'] = _brand_code.get(brand_var.get(), '')
        root.destroy()

    # 布局
    frame = tk.Frame(root, padx=26, pady=18)
    frame.pack(fill='both', expand=True)

    tk.Label(frame, text="请选择水印样式（可多选）：", font=base_font).pack(anchor='w', pady=(0, 8))

    sf = ttk.LabelFrame(frame, text=" 水印样式 ", padding=10)
    sf.pack(fill='x', pady=(0, 12))
    for i, (key, label, desc) in enumerate(STYLE_OPTIONS):
        row = i // 2
        col = i % 2
        cell = ttk.Frame(sf)
        cell.grid(row=row, column=col, sticky='w', padx=(0, 30), pady=3)
        ttk.Checkbutton(cell, text=label, variable=style_vars[key]).pack(anchor='w')
        ttk.Label(cell, text=desc, foreground='#808080', font=small_font).pack(anchor='w', padx=(20, 0))

    # 品牌Logo行（本次处理强制指定）
    brand_row = ttk.Frame(frame)
    brand_row.pack(fill='x', pady=(0, 12))
    ttk.Label(brand_row, text="品牌Logo:").pack(side='left', padx=(2, 6))
    ttk.Combobox(brand_row, textvariable=brand_var,
                 values=[n for n, _ in BRAND_OPTIONS],
                 state='readonly', width=14).pack(side='left')
    ttk.Label(brand_row, text="（自动=按EXIF识别）", foreground='#808080',
              font=small_font).pack(side='left', padx=8)

    btns = ttk.Frame(frame)
    btns.pack()
    ttk.Button(btns, text="开始处理", command=confirm, width=14).pack(side='left', padx=6)
    ttk.Button(btns, text="取消", command=root.destroy, width=14).pack(side='left', padx=6)

    # 居中显示
    root.update_idletasks()
    w = max(root.winfo_reqwidth(), 480)
    h = root.winfo_reqheight()
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 3
    root.geometry(f"{w}x{h}+{x}+{y}")

    root.mainloop()
    return result['style'], result['brand']


def select_paths_gui() -> tuple:
    """
    弹出图形界面让用户选择水印样式、照片文件（可多选）或文件夹

    窗口内容：
    - 水印样式选择（可多选，默认勾选配置中的样式）
    - 可直接把照片/文件夹拖到窗口上（tkinterdnd2），或点按钮选择
    - 品牌Logo下拉：本次处理可强制指定品牌Logo
    - 选好后点「开始处理」

    Returns:
        (路径列表, 样式字符串, 品牌代码)；用户取消时返回 ([], '', '')
    """
    import tkinter as tk
    from tkinter import filedialog, font as tkfont

    # 启用窗口拖放（tkinterdnd2）；不支持时退回普通窗口
    try:
        from tkinterdnd2 import TkinterDnD, DND_FILES
        root = TkinterDnD.Tk()
        drop_supported = True
    except Exception:
        root = tk.Tk()
        drop_supported = False
    root.title("Photo Watermark - 选择照片")
    root.attributes('-topmost', True)
    root.resizable(False, False)

    # 尝试使用中文字体（微软雅黑），失败则用系统默认
    try:
        families = set(tkfont.families(root))
        for name in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei", "微软雅黑"):
            if name in families:
                tkfont.nametofont("TkDefaultFont").configure(family=name, size=10)
                break
    except Exception:
        pass

    # 命名字体（标题粗体、说明小字）
    base_font = tkfont.nametofont("TkDefaultFont")
    bold_font = base_font.copy()
    bold_font.configure(weight='bold')
    big_font = base_font.copy()
    big_font.configure(size=14, weight='bold')
    small_font = base_font.copy()
    small_font.configure(size=9)

    result = {'paths': None, 'style': '', 'brand': ''}
    status_var = tk.StringVar(
        value="可把照片/文件夹直接拖到本窗口" if drop_supported else "点击下方按钮选择照片/文件夹")

    # 水印样式选项（可多选，默认勾选配置中的样式）
    styles = STYLE_OPTIONS
    default_styles = [s.strip() for s in DEFAULT_STYLE.split(',') if s.strip()] or ['strip']
    style_vars = {}
    for key, _, _ in styles:
        style_vars[key] = tk.BooleanVar(value=(key in default_styles))

    # 品牌Logo选择（本次处理强制使用，自动=按EXIF品牌识别）
    _brand_code = {n: c for n, c in BRAND_OPTIONS}
    brand_var = tk.StringVar(value='自动')

    def get_style():
        checked = [k for k, _, _ in styles if style_vars[k].get()]
        return ','.join(checked) if checked else 'strip'

    def set_files(paths):
        """设置选中的路径并更新界面（按钮选择或窗口拖放）"""
        result['paths'] = list(paths)
        status_var.set(f"已选择 {len(result['paths'])} 个文件/文件夹，可调整样式后点「开始处理」")
        btn_start.config(state='normal')

    def pick_files():
        filetypes = [
            ("图片文件", "*.jpg *.jpeg *.png *.tiff *.bmp"),
            ("所有文件", "*.*"),
        ]
        files = filedialog.askopenfilenames(
            title="选择要处理的照片（可多选）",
            filetypes=filetypes,
            parent=root,
        )
        if files:
            set_files(files)

    def pick_folder():
        folder = filedialog.askdirectory(
            title="选择要批量处理的文件夹",
            parent=root,
        )
        if folder:
            set_files([folder])

    def on_drop(event):
        """窗口拖放：接收拖入的照片/文件夹路径"""
        try:
            paths = root.tk.splitlist(event.data)
        except Exception:
            paths = [p for p in event.data.split() if p]
        if paths:
            set_files(paths)

    def confirm_start():
        """开始处理：收集样式/品牌选择并关闭窗口"""
        result['style'] = get_style()
        result['brand'] = _brand_code.get(brand_var.get(), '')
        root.destroy()

    if drop_supported:
        root.drop_target_register(DND_FILES)
        root.dnd_bind('<<Drop>>', on_drop)

    def open_settings():
        """打开设置窗口，保存后刷新样式勾选"""
        changed = open_settings_window(root)
        if changed:
            new_defaults = [s.strip() for s in DEFAULT_STYLE.split(',') if s.strip()] or ['strip']
            for key, _, _ in styles:
                style_vars[key].set(key in new_defaults)

    # ========== 界面布局 ==========
    from tkinter import ttk

    try:
        style = ttk.Style(root)
        try:
            style.theme_use('vista')  # Windows 现代主题（不存在则跳过）
        except tk.TclError:
            pass
        style.configure('Header.TLabel', font=big_font, foreground='#2b579a')
        style.configure('Sub.TLabel', font=small_font, foreground='#666666')
        style.configure('Desc.TLabel', font=small_font, foreground='#808080')
        style.configure('Primary.TButton', font=base_font, padding=(14, 8))
        style.configure('Normal.TButton', font=base_font, padding=(12, 8))
    except Exception:
        style = None

    # 标题区
    header = tk.Frame(root, bg='#eef3fb')
    header.pack(fill='x')
    tk.Label(header, text="Photo Watermark  ·  照片水印", font=big_font,
             bg='#eef3fb', fg='#2b579a').pack(pady=(14, 2))
    tk.Label(header, text="给相机照片添加拍摄参数水印边框", font=small_font,
             bg='#eef3fb', fg='#666666').pack(pady=(0, 12))

    frame = tk.Frame(root, padx=24, pady=16)
    frame.pack(fill='both', expand=True)

    # 样式选择区
    sf = ttk.LabelFrame(frame, text=" 水印样式（可多选） ", padding=10)
    sf.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 10))
    for i, (key, label, desc) in enumerate(styles):
        row = i // 2
        col = i % 2
        cell = ttk.Frame(sf)
        cell.grid(row=row, column=col, sticky='w', padx=(0, 30), pady=3)
        ttk.Checkbutton(cell, text=label, variable=style_vars[key]).pack(anchor='w')
        ttk.Label(cell, text=desc, style='Desc.TLabel').pack(anchor='w', padx=(20, 0))

    # 照片选择区
    pf = ttk.LabelFrame(frame, text=" 选择要处理的照片 ", padding=10)
    pf.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(0, 10))
    btns_row = ttk.Frame(pf)
    btns_row.pack(fill='x')
    ttk.Button(btns_row, text="选择照片（可多选）", command=pick_files,
               style='Primary.TButton').pack(side='left', padx=6)
    ttk.Button(btns_row, text="选择文件夹（批量处理）", command=pick_folder,
               style='Primary.TButton').pack(side='left', padx=6)
    # 品牌Logo行（本次处理强制指定，自动=按EXIF识别）
    brand_row = ttk.Frame(pf)
    brand_row.pack(fill='x', pady=(8, 0))
    ttk.Label(brand_row, text="品牌Logo:").pack(side='left', padx=(6, 4))
    ttk.Combobox(brand_row, textvariable=brand_var,
                 values=[n for n, _ in BRAND_OPTIONS],
                 state='readonly', width=14).pack(side='left')
    ttk.Label(brand_row, text="（选「哈苏」等可强制使用该品牌Logo）",
              style='Desc.TLabel').pack(side='left', padx=8)
    # 拖放/选择状态提示
    ttk.Label(pf, textvariable=status_var, style='Desc.TLabel').pack(
        anchor='w', padx=6, pady=(10, 2))

    # 底部按钮
    btm = ttk.Frame(frame)
    btm.grid(row=2, column=0, columnspan=2, sticky='ew')
    ttk.Button(btm, text="设置...", command=open_settings,
               style='Normal.TButton').pack(side='left')
    btn_start = ttk.Button(btm, text="开始处理", command=confirm_start,
                           style='Primary.TButton', state='disabled')
    btn_start.pack(side='right', padx=(0, 6))
    ttk.Button(btm, text="取消", command=root.destroy,
               style='Normal.TButton').pack(side='right')

    # 窗口居中显示，并保证足够大
    root.update_idletasks()
    w = max(root.winfo_reqwidth(), 540)
    h = root.winfo_reqheight()
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 3
    root.geometry(f"{w}x{h}+{x}+{y}")

    root.mainloop()
    return result['paths'] or [], result['style'], result['brand']


def notify(title: str, message: str, is_error: bool = False):
    """
    完成/错误提示：控制台隐藏时用弹窗，否则打印到控制台

    Args:
        title: 弹窗标题
        message: 提示内容
        is_error: 是否为错误提示
    """
    if CONSOLE_HIDDEN:
        try:
            import tkinter as tk
            from tkinter import messagebox
            tip = tk.Tk()
            tip.withdraw()
            tip.attributes('-topmost', True)
            if is_error:
                messagebox.showerror(title, message, parent=tip)
            else:
                messagebox.showinfo(title, message, parent=tip)
            tip.destroy()
        except Exception:
            pass
    else:
        print(message)


def process_multiple_files(
    file_list: list,
    style: str = 'strip',
    custom_text: str = '',
    **kwargs,
) -> Tuple[int, int, float]:
    """
    处理多张图片，输出到各自所在目录（与照片放一起，自动命名）

    Args:
        file_list: 图片文件路径列表
        style: 边框样式
        custom_text: 自定义文字
        **kwargs: 其他参数

    Returns:
        (成功数量, 跳过数量, 总数量, 用时秒数)
    """
    files = sorted(Path(p) for p in file_list)
    success_count = 0
    skipped_count = 0
    start_time = datetime.now()

    first_style = style.split(',')[0].strip() if style else 'strip'

    for i, img_file in enumerate(files, 1):
        print(f"[{i}/{len(files)}] 处理: {img_file.name}")
        output_name = OUTPUT_FILENAME_FORMAT.format(name=img_file.stem, style=first_style)
        output_file = img_file.parent / f"{output_name}{img_file.suffix}"

        status = process_single_image(str(img_file), str(output_file), style, custom_text, **kwargs)
        if status == 'ok':
            success_count += 1
        elif status == 'skipped':
            skipped_count += 1
        else:
            print(f"  -> 处理失败")

    elapsed_time = (datetime.now() - start_time).total_seconds()
    return success_count, skipped_count, len(files), elapsed_time


def main():
    # 显示项目信息
    print("=" * 50)
    print("  Photo Watermark - 相机照片水印边框生成器")
    print("  版本: v1.5.0")
    print("  项目: https://github.com/go-farther-and-farther/photo_watermark")
    print("=" * 50)
    print()

    # 显示配置状态提示
    tips = []
    if not DEFAULT_INPUT:
        tips.append("默认输入路径未设置，将弹出窗口选择照片")
    if not DEFAULT_OUTPUT:
        tips.append("默认输出路径未设置，将自动保存（照片旁/watermark_output）")
    if not DEFAULT_LOGO:
        tips.append("DEFAULT_LOGO 未设置，将根据品牌自动匹配")
    if DEFAULT_TEXT:
        tips.append(f"自定义文字: {DEFAULT_TEXT}")

    if tips:
        print("[提示] 提示（可在 水印设置.ini 中修改）:")
        for tip in tips:
            print(f"   - {tip}")
        print()

    parser = argparse.ArgumentParser(
        description='相机照片水印边框生成器',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 白底黑字条形边框样式
  python photo_watermark.py input.jpg --style strip --text "©摄影师"

  # 半透明水印
  python photo_watermark.py input.jpg --style transparent --position bottom-right

  # 纯色边框样式
  python photo_watermark.py input.jpg --style border --border-color black --text "©摄影师"

  # 批量处理
  python photo_watermark.py ./photos/ --style strip --text "©摄影师" --output ./output/
        """,
    )

    parser.add_argument(
        'input',
        nargs='*',
        default=None,
        help='输入图片路径或文件夹路径（可多个，支持直接把照片/文件夹拖到exe上；留空则弹出窗口选择）',
    )
    parser.add_argument(
        '-o', '--output',
        default=DEFAULT_OUTPUT if DEFAULT_OUTPUT else None,
        help='输出路径（留空则自动处理：照片输出到所在目录，文件夹输出到其下watermark_output）',
    )
    parser.add_argument(
        '-s', '--style',
        default=argparse.SUPPRESS,
        help='边框样式: strip(白底条形), transparent(半透明), border(纯色边框), blur(模糊边框)，多样式用逗号分隔如 strip,blur；不指定且拖入多个文件时弹出样式选择窗口',
    )
    parser.add_argument(
        '-t', '--text',
        default=DEFAULT_TEXT,
        help='自定义水印文字（如摄影师名字），默认读取config.py的DEFAULT_TEXT',
    )
    parser.add_argument(
        '-p', '--position',
        choices=['top-left', 'top-right', 'bottom-left', 'bottom-right'],
        default=TRANSPARENT_POSITION,
        help='半透明水印的位置',
    )
    parser.add_argument(
        '--border-color',
        default=None,
        help='边框颜色（black/white/gray或RGB格式如255,255,255），默认使用config.py配置',
    )
    parser.add_argument(
        '--text-color',
        default=None,
        help='文字颜色，默认使用config.py配置',
    )
    parser.add_argument(
        '--opacity',
        type=int,
        default=TRANSPARENT_OPACITY,
        help='半透明水印的透明度（0-255）',
    )
    parser.add_argument(
        '--quality',
        type=int,
        default=JPEG_QUALITY,
        help='JPEG输出质量（1-100）',
    )
    parser.add_argument(
        '--logo',
        default=DEFAULT_LOGO,
        help='Logo图片路径（留空则根据品牌自动匹配）',
    )
    parser.add_argument(
        '--preview',
        action='store_true',
        help='预览模式：显示效果但不保存文件',
    )

    args = parser.parse_args()

    # 是否显式指定了样式（未指定且拖入多个文件时，弹出样式选择窗口）
    style_explicit = hasattr(args, 'style')
    if not style_explicit:
        args.style = DEFAULT_STYLE

    # ===== 解析输入路径（支持多个，可把照片/文件夹拖到exe上） =====
    input_path = None
    multi_files = None
    multi_dirs = None
    gui_mode = False  # GUI选择模式：处理完不退出，可继续选择

    raw_inputs = args.input
    if isinstance(raw_inputs, str):
        raw_inputs = [raw_inputs]
    inputs = [p for p in (raw_inputs or []) if p]
    if not inputs and DEFAULT_INPUT:
        inputs = [DEFAULT_INPUT]

    if inputs:
        paths = [Path(p) for p in inputs]
        for m in [p for p in paths if not p.exists()]:
            print(f"路径不存在: {m}")
        existing = [p for p in paths if p.exists()]
        if existing:
            files = sorted([p for p in existing if p.is_file()])
            dirs = sorted([p for p in existing if p.is_dir()])
            if files:
                multi_files = files
            if dirs:
                multi_dirs = dirs

    # 无有效输入 → 弹出图形界面选择照片/文件夹
    gui_brand = ''  # GUI中选择的强制品牌Logo（本次处理生效）
    drag_brand = ''  # 拖拽弹窗中选择的强制品牌Logo
    if not input_path and not multi_files and not multi_dirs:
        try:
            selected, selected_style, gui_brand = select_paths_gui()
        except ImportError:
            print("错误: 未指定输入路径，请通过命令行参数或在 水印设置.ini 中设置「默认输入路径」")
            sys.exit(1)

        if not selected:
            print("未选择任何内容，程序退出")
            sys.exit(0)

        gui_mode = True

        # 弹窗中选择的样式覆盖默认样式
        if selected_style:
            args.style = selected_style
            print(f"已选择样式: {args.style}")

        if len(selected) == 1:
            input_path = Path(selected[0])
            print(f"已选择: {input_path}")
        else:
            multi_files = selected
            print(f"已选择 {len(multi_files)} 张照片")

    # 拖拽/命令行传入多个路径且未显式指定样式 → 弹出样式选择窗口（跳过选图）
    total_inputs = (len(multi_files) if multi_files else 0) + (len(multi_dirs) if multi_dirs else 0)
    if total_inputs > 1 and not style_explicit and not gui_mode:
        print(f"收到 {total_inputs} 个路径，请选择水印样式...")
        try:
            style_choice, drag_brand = choose_style_gui(args.style)
        except ImportError:
            style_choice = ''
            drag_brand = ''
        if style_choice:
            args.style = style_choice
            print(f"已选择样式: {args.style}")
        else:
            print("已取消，退出")
            sys.exit(0)

    # 验证样式参数
    valid_styles = {'strip', 'transparent', 'border', 'blur'}
    for s in args.style.split(','):
        if s.strip() not in valid_styles:
            print(f"错误: 未知样式 '{s.strip()}'，支持的样式: {', '.join(valid_styles)}")
            sys.exit(1)

    # 解析颜色（None表示使用config.py默认值）
    border_color = parse_color(args.border_color) if args.border_color else None
    text_color = parse_color(args.text_color) if args.text_color else None

    # 准备参数
    kwargs = {
        'position': args.position,
        'border_color': border_color,
        'text_color': text_color,
        'opacity': args.opacity,
        'quality': args.quality,
        'logo_path': args.logo,
    }
    # GUI/拖拽弹窗中强制指定的品牌Logo（本次处理覆盖自动识别）
    if gui_brand or drag_brand:
        kwargs['logo_path'] = gui_brand or drag_brand
        print(f"强制品牌Logo: {kwargs['logo_path']}")

    # 显示当前使用的配置
    print(f"[配置] 当前配置:")
    print(f"   样式: {args.style}")
    if multi_files or multi_dirs:
        n = (len(multi_files) if multi_files else 0) + (len(multi_dirs) if multi_dirs else 0)
        if gui_mode:
            print(f"   输入: {n} 个路径（图形界面选择）")
        else:
            print(f"   输入: {n} 个路径（拖拽/命令行传入）")
    else:
        print(f"   输入: {input_path}")
    if args.text:
        print(f"   自定义文字: {args.text}")
    if args.preview:
        print(f"   模式: 预览（不保存）")
    print()

    # 预览模式：只处理第一张图片并显示
    if args.preview and input_path and input_path.is_file():
        print("预览模式：显示效果但不保存")

        # 智能样式选择
        style = args.style
        if SMART_STYLE == 'auto':
            style = get_smart_style(str(input_path))
            print(f"  智能样式: {style}（根据照片方向）")

        # 读取图片
        image = Image.open(input_path)
        if image.mode in ('RGBA', 'P'):
            image = image.convert('RGB')
        exif_data = read_exif(str(input_path))
        orientation = exif_data.get('orientation', 1)
        if orientation != 1:
            image = auto_rotate_image(image, orientation)

        # 应用水印
        if style == 'strip':
            result = apply_white_border(image, exif_data, args.text, logo_path=args.logo)
        elif style == 'transparent':
            result = apply_transparent_watermark(image, args.text or format_exif_text(exif_data), **kwargs)
        elif style == 'border':
            result = apply_color_border(image, exif_data, args.text, **kwargs)
        elif style == 'blur':
            result = apply_blur_border(image, exif_data, args.text, **kwargs)

        # 显示预览
        result.show()
        print("预览窗口已打开，关闭后程序继续")
        sys.exit(0)

    # ========== 处理循环 ==========
    # GUI选择模式下处理完不退出，自动回到选择窗口继续用；命令行/拖拽模式处理一次后退出
    while True:
        if multi_files or multi_dirs:
            # 多文件/多文件夹处理（图形界面多选或拖拽传入）
            total_success = 0
            total_skipped = 0
            total_count = 0
            start_time = datetime.now()

            if multi_files:
                print(f"批量处理 {len(multi_files)} 张照片（输出到照片所在目录）")
                print("-" * 50)
                s, sk, t, _ = process_multiple_files(multi_files, args.style, args.text, **kwargs)
                total_success += s
                total_skipped += sk
                total_count += t

            for d in (multi_dirs or []):
                print(f"批量处理文件夹: {d}")
                print(f"输出目录: {d / 'watermark_output'}")
                print("-" * 50)
                s, sk, t, _ = batch_process(
                    str(d), str(d / 'watermark_output'), args.style, args.text, **kwargs
                )
                total_success += s
                total_skipped += sk
                total_count += t

            elapsed_time = (datetime.now() - start_time).total_seconds()
            print("-" * 50)
            if total_success == total_count:
                print(f"[成功] 全部完成: {total_success}/{total_count} 张照片处理成功")
            else:
                print(f"[警告] 部分完成: {total_success}/{total_count} 张照片处理成功")
            if total_skipped:
                print(f"[跳过] {total_skipped} 张照片输出已存在，未重新处理（可删除旧文件或在设置中开启覆盖）")
            print(f"[用时]  用时: {elapsed_time:.1f} 秒")

            # 控制台隐藏时自动打开所在文件夹，方便查看结果
            if CONSOLE_HIDDEN and AUTO_OPEN_OUTPUT and sys.platform == 'win32':
                first_dir = Path(multi_files[0]).parent if multi_files else multi_dirs[0]
                os.startfile(os.path.abspath(str(first_dir)))
            # 控制台隐藏时提示结果（有失败或跳过时）
            if CONSOLE_HIDDEN and (total_success < total_count or total_skipped):
                msg = f"处理完成：{total_success}/{total_count} 张成功"
                if total_skipped:
                    msg += f"，{total_skipped} 张已存在被跳过"
                notify("完成", msg)

        elif input_path.is_file():
            # 单张图片处理
            # 智能样式：按照片方向自动选择（竖版照片避免白条样式过粗）
            if SMART_STYLE == 'auto':
                args.style = get_smart_style(str(input_path))
                print(f"  智能样式: {args.style}（根据照片方向）")
            if args.output:
                output_path = args.output
            else:
                # 使用配置的命名格式
                output_name = OUTPUT_FILENAME_FORMAT.format(
                    name=input_path.stem,
                    style=args.style,
                )
                output_path = str(input_path.parent / f"{output_name}{input_path.suffix}")

            print(f"处理图片: {input_path.name}")
            status = process_single_image(
                str(input_path), output_path, args.style, args.text, **kwargs
            )
            if status == 'ok':
                print(f"[成功] 完成! 保存到: {output_path}")
                # 控制台隐藏时自动打开所在文件夹，方便查看结果
                if CONSOLE_HIDDEN and AUTO_OPEN_OUTPUT and sys.platform == 'win32':
                    os.startfile(os.path.abspath(str(Path(output_path).parent)))
            elif status == 'skipped':
                print(f"[跳过] 输出文件已存在，未重新处理: {Path(output_path).name}")
                if CONSOLE_HIDDEN:
                    notify("提示", f"输出文件已存在，未重新处理:\n{Path(output_path).name}")
            else:
                notify("处理失败", f"处理失败: {input_path.name}", is_error=True)
                if not gui_mode:
                    sys.exit(1)
                # GUI模式：提示后回到选择窗口

        elif input_path.is_dir():
            # 批量处理
            if args.output:
                output_dir = args.output
            else:
                output_dir = str(input_path / "watermark_output")

            print(f"批量处理文件夹: {input_path}")
            print(f"输出目录: {output_dir}")
            print("-" * 50)

            success, skipped, total, elapsed_time = batch_process(
                str(input_path), output_dir, args.style, args.text, **kwargs
            )

            print("-" * 50)
            if success == total:
                print(f"[成功] 全部完成: {success}/{total} 张图片处理成功")
            else:
                print(f"[警告]  部分完成: {success}/{total} 张图片处理成功")
            if skipped:
                print(f"[跳过] {skipped} 张图片输出已存在，未重新处理")
            print(f"[用时]  用时: {elapsed_time:.1f} 秒")
            print(f"[目录] 输出目录: {output_dir}")

            # 自动打开输出目录（Windows）
            if AUTO_OPEN_OUTPUT and sys.platform == 'win32':
                os.startfile(os.path.abspath(output_dir))
            # 控制台隐藏时提示结果（有失败或跳过时）
            if CONSOLE_HIDDEN and (success < total or skipped):
                msg = f"处理完成：{success}/{total} 张成功"
                if skipped:
                    msg += f"，{skipped} 张已存在被跳过"
                notify("完成", msg)

        else:
            notify("错误", f"无效的输入路径 - {args.input}", is_error=True)
            sys.exit(1)

        # 命令行模式：处理一次后退出
        if not gui_mode:
            break

        # GUI模式：处理完成，回到选择窗口继续使用
        print()
        print("处理完成！可继续选择其他照片（点「取消」退出）")
        try:
            selected, selected_style, selected_brand = select_paths_gui()
        except ImportError:
            break
        if not selected:
            print("已退出")
            break
        if selected_style:
            args.style = selected_style
        if selected_brand:
            kwargs['logo_path'] = selected_brand  # 本次处理强制品牌Logo
        if len(selected) == 1:
            input_path = Path(selected[0])
            multi_files = None
            print(f"已选择: {input_path}")
        else:
            multi_files = selected
            input_path = None
            print(f"已选择 {len(multi_files)} 张照片")

    # exe命令行模式：等待用户确认（隐藏控制台时用弹窗提示完成）
    if getattr(sys, 'frozen', False) and not gui_mode:
        if CONSOLE_HIDDEN:
            notify("完成", "处理完成！")
        else:
            print()
            print("=" * 50)
            print("处理完成！")
            print("=" * 50)
            input("按回车键关闭...")


if __name__ == '__main__':
    main()
