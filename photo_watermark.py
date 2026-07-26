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
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont

try:
    import exifread
except ImportError:
    print("错误: 请安装 exifread 库 - pip install exifread")
    sys.exit(1)


def get_base_dir() -> Path:
    """获取程序所在目录（兼容exe和源码运行）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包的 exe
        return Path(sys.executable).parent
    else:
        # 源码运行
        return Path(__file__).parent

# 导入配置
try:
    from config import *
except ImportError:
    # 默认配置（如果config.py不存在）
    DEFAULT_STYLE = 'strip'
    DEFAULT_LOGO = ''
    DEFAULT_INPUT = ''
    DEFAULT_OUTPUT = ''
    JPEG_QUALITY = 98
    FONT_SIZE_RATIO = 3
    # white 样式
    BORDER_HEIGHT_RATIO = 0.08
    LEFT_MARGIN_RATIO = 0.025
    LINE_SPACING = 6
    RIGHT_MARGIN_RATIO = 0.025
    LOGO_PARAMS_SPACING_RATIO = 0.03
    LOGO_HEIGHT_RATIO = 0.7
    VERTICAL_OFFSET_RATIO = 0.15
    COLOR_CAMERA = (30, 30, 30)
    COLOR_LENS = (120, 120, 120)
    COLOR_PARAMS = (30, 30, 30)
    COLOR_DATE = (100, 100, 100)
    COLOR_BORDER = (255, 255, 255)
    # transparent 样式
    TRANSPARENT_POSITION = 'bottom-right'
    TRANSPARENT_OPACITY = 128
    TRANSPARENT_FONT_RATIO = 0.03
    TRANSPARENT_TEXT_COLOR = (255, 255, 255)
    TRANSPARENT_MARGIN_RATIO = 0.02
    # border 样式
    BORDER_FRAME_COLOR = (0, 0, 0)
    BORDER_TEXT_COLOR = (255, 255, 255)
    BORDER_SIDE_RATIO = 0.04
    BORDER_BOTTOM_RATIO = 0.08
    # blur 样式（模糊边框）
    BLUR_BORDER_RATIO = 0.06
    BLUR_INTENSITY = 15
    BLUR_TEXT_COLOR = (255, 255, 255)
    BLUR_TEXT_SHADOW = True
    BLUR_CORNER_RADIUS = 0.05

# blur 样式内部常量
BLUR_BOTTOM_RATIO_MULTIPLIER = 1.8  # 底部边框宽度倍数
BLUR_BRIGHTNESS_FACTOR = 0.85       # 亮度降低因子
BLUR_DOWNSAMPLE_FACTOR = 4          # 缩小倍数（1/4）

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

    except Exception as e:
        print(f"警告: 读取EXIF信息失败 - {e}")

    return exif_data


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

    # Windows系统字体路径（粗体用 msyhbd.ttc）
    font_paths = [
        'C:/Windows/Fonts/msyhbd.ttc' if bold else 'C:/Windows/Fonts/msyh.ttc',  # 微软雅黑
        'C:/Windows/Fonts/simhei.ttf',     # 黑体
        'C:/Windows/Fonts/simsun.ttc',     # 宋体
        'C:/Windows/Fonts/arial.ttf',      # Arial
    ]

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
        'RICOH': 'pentax_logo.png',        # 宾得已被理光收购
        'PANASONIC': 'panasonic_logo.png',
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

    return '  •  '.join(parts) if parts else ''


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
    border_height = int(height * BORDER_HEIGHT_RATIO)

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

    # 创建新图片（原图 + 底部边框）
    new_height = height + border_height
    new_image = Image.new('RGB', (width, new_height), COLOR_BORDER)

    # 粘贴原图
    new_image.paste(image, (0, 0))

    # 绘制文字
    draw = ImageDraw.Draw(new_image)

    # ========== 左侧：镜头和相机信息 ==========
    lens_text = exif_data.get('lens', '')
    camera_text = exif_data.get('camera', '')

    # 相机型号（第一行，深色）
    if camera_text:
        draw.text(
            (left_margin, margin_top),
            camera_text,
            fill=COLOR_CAMERA,
            font=font_bold,
        )

    # 镜头型号（第二行，灰色）
    if lens_text:
        draw.text(
            (left_margin, margin_top + font_size + LINE_SPACING),
            lens_text,
            fill=COLOR_LENS,
            font=font,
        )

    # ========== 右侧：参数 + Logo + 日期 ==========
    # 拍摄参数（焦距 光圈 快门 ISO）
    params_text = get_params_text(exif_data)

    # 日期时间
    datetime_text = exif_data.get('datetime', '')

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
            print(f"  警告: 加载logo失败 - {e}")

    # 绘制日期时间（第二行右侧，参数下方）
    if datetime_text:
        # 格式化日期时间显示 - 只显示日期，不显示时间
        # EXIF格式: "2026:04:08 12:23:00" -> "2026-04-08"
        datetime_display = datetime_text.replace(':', '-', 2)  # 只替换前两个冒号为日期分隔
        if len(datetime_display) > 10:
            datetime_display = datetime_display[:10]  # 截取到日期部分

        bbox = draw.textbbox((0, 0), datetime_display, font=font)
        text_width = bbox[2] - bbox[0]
        # 日期时间与参数对齐
        date_x = right_x - text_width
        draw.text(
            (date_x, margin_top + font_size + LINE_SPACING),
            datetime_display,
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

    # 创建新图片
    new_image = Image.new('RGB', (new_width, new_height), border_color)

    # 粘贴原图
    new_image.paste(image, (border_size, border_size))

    # 绘制文字
    draw = ImageDraw.Draw(new_image)
    font_size = max(12, bottom_border // 3)
    font = get_font(font_size)

    # 组合显示文本
    exif_text = format_exif_text(exif_data)
    display_parts = []
    if exif_text:
        display_parts.append(exif_text)
    if custom_text:
        display_parts.append(custom_text)
    display_text = '  |  '.join(display_parts)

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

    # 组合显示文本
    params_text = get_params_text(exif_data)

    datetime_text = exif_data.get('datetime', '')
    if datetime_text:
        datetime_text = datetime_text.replace(':', '-', 2)[:10]

    # 绘制拍摄参数（底部居中，第一行）
    if params_text:
        bbox = draw.textbbox((0, 0), params_text, font=font)
        text_width = bbox[2] - bbox[0]
        x = (new_width - text_width) // 2
        y = height + border_size + (bottom_border - font_size * 2 - 8) // 2

        # 文字阴影
        if text_shadow:
            shadow_offset = max(1, font_size // 15)
            draw.text((x + shadow_offset, y + shadow_offset), params_text,
                      fill=(0, 0, 0, 180), font=font)

        draw.text((x, y), params_text, fill=text_color, font=font)

    # 绘制日期（底部居中，第二行）
    if datetime_text:
        bbox = draw.textbbox((0, 0), datetime_text, font=font)
        text_width = bbox[2] - bbox[0]
        x = (new_width - text_width) // 2
        y = height + border_size + (bottom_border - font_size * 2 - 8) // 2 + font_size + 6

        if text_shadow:
            shadow_offset = max(1, font_size // 15)
            draw.text((x + shadow_offset, y + shadow_offset), datetime_text,
                      fill=(0, 0, 0, 180), font=font)

        draw.text((x, y), datetime_text, fill=text_color, font=font)

    return new_image


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
        style: 边框样式（strip, transparent, border, blur）
        custom_text: 自定义文字
        **kwargs: 其他参数

    Returns:
        是否成功
    """
    try:
        # 读取图片
        image = Image.open(input_path)

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
            # logo_path 为空，根据品牌自动匹配
            brand = exif_data.get('brand', '')
            if brand:
                logo_path = get_logo_by_brand(brand)
                if logo_path:
                    print(f"  自动选择Logo: {Path(logo_path).name} (品牌: {brand})")
                else:
                    print(f"  未找到品牌Logo: {brand}")

        # 根据样式应用水印
        if style == 'strip':
            result = apply_white_border(
                image, exif_data, custom_text,
                logo_path=logo_path,
            )
        elif style == 'transparent':
            result = apply_transparent_watermark(
                image, custom_text or format_exif_text(exif_data),
                position=kwargs.get('position', ''),
                opacity=kwargs.get('opacity', 0),
                font_ratio=kwargs.get('font_ratio', 0.0),
                text_color=kwargs.get('text_color', None),
                margin_ratio=kwargs.get('margin_ratio', 0.0),
            )
        elif style == 'border':
            result = apply_color_border(
                image, exif_data, custom_text,
                border_color=kwargs.get('border_color', None),
                text_color=kwargs.get('text_color', None),
                border_side_ratio=kwargs.get('border_side_ratio', 0.0),
                border_bottom_ratio=kwargs.get('border_bottom_ratio', 0.0),
            )
        elif style == 'blur':
            result = apply_blur_border(
                image, exif_data, custom_text,
                border_ratio=kwargs.get('border_ratio', 0.0),
                blur_intensity=kwargs.get('blur_intensity', 0),
                text_color=kwargs.get('text_color', None),
                text_shadow=kwargs.get('text_shadow', True),
            )
        else:
            print(f"错误: 未知的样式 '{style}'")
            return False

        # 保存图片
        output_dir = Path(output_path).parent
        output_dir.mkdir(parents=True, exist_ok=True)

        # 根据输出格式选择保存参数
        output_ext = Path(output_path).suffix.lower()
        if output_ext in ('.jpg', '.jpeg'):
            result.save(output_path, 'JPEG', quality=kwargs.get('quality', JPEG_QUALITY))
        elif output_ext == '.png':
            result.save(output_path, 'PNG')
        else:
            result.save(output_path)

        return True

    except Exception as e:
        print(f"错误: 处理图片失败 - {e}")
        return False


def batch_process(
    input_dir: str,
    output_dir: str,
    style: str = 'strip',
    custom_text: str = '',
    **kwargs,
) -> Tuple[int, int]:
    """
    批量处理文件夹中的图片

    Args:
        input_dir: 输入文件夹路径
        output_dir: 输出文件夹路径
        style: 边框样式
        custom_text: 自定义文字
        **kwargs: 其他参数

    Returns:
        (成功数量, 总数量)
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
        print(f"警告: 在 {input_dir} 中没有找到支持的图片文件")
        return 0, 0

    print(f"找到 {len(image_files)} 张图片")

    success_count = 0
    for i, img_file in enumerate(image_files, 1):
        # 生成输出文件名（包含样式名）
        output_file = output_path / f"{img_file.stem}_{style}_watermark{img_file.suffix}"

        print(f"[{i}/{len(image_files)}] 处理: {img_file.name}")

        if process_single_image(
            str(img_file), str(output_file), style, custom_text, **kwargs
        ):
            success_count += 1
            print(f"  -> 保存到: {output_file.name}")
        else:
            print(f"  -> 处理失败")

    return success_count, len(image_files)


def parse_color(color_str: str) -> Tuple[int, int, int]:
    """
    解析颜色字符串

    Args:
        color_str: 颜色字符串（如 'black', 'white', '255,255,255'）

    Returns:
        RGB颜色元组
    """
    colors = {
        'black': (0, 0, 0),
        'white': (255, 255, 255),
        'red': (255, 0, 0),
        'green': (0, 128, 0),
        'blue': (0, 0, 255),
        'gray': (128, 128, 128),
        'grey': (128, 128, 128),
    }

    color_str = color_str.lower().strip()
    if color_str in colors:
        return colors[color_str]

    # 尝试解析RGB格式
    try:
        parts = [int(x.strip()) for x in color_str.split(',')]
        if len(parts) == 3 and all(0 <= x <= 255 for x in parts):
            return tuple(parts)
    except (ValueError, AttributeError):
        pass

    print(f"警告: 无法解析颜色 '{color_str}'，使用默认黑色")
    return (0, 0, 0)


def main():
    # 显示项目信息
    print("=" * 50)
    print("  Photo Watermark - 相机照片水印边框生成器")
    print("  版本: v1.1.1")
    print("  项目: https://github.com/go-farther-and-farther/photo_watermark")
    print("=" * 50)
    print()

    # 显示配置状态提示
    tips = []
    if not DEFAULT_INPUT:
        tips.append("DEFAULT_INPUT 未设置，将弹出选择对话框")
    if not DEFAULT_OUTPUT:
        tips.append("DEFAULT_OUTPUT 未设置，将自动命名输出文件")
    if not DEFAULT_LOGO:
        tips.append("DEFAULT_LOGO 未设置，将根据品牌自动匹配")

    if tips:
        print("💡 提示（可在 config.py 中修改）:")
        for tip in tips:
            print(f"   • {tip}")
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
        nargs='?',
        default=DEFAULT_INPUT if DEFAULT_INPUT else None,
        help='输入图片路径或文件夹路径（默认: config.py配置，留空则弹出选择）',
    )
    parser.add_argument(
        '-o', '--output',
        default=DEFAULT_OUTPUT if DEFAULT_OUTPUT else None,
        help='输出路径（默认: config.py配置，留空则在输入路径同级创建output文件夹）',
    )
    parser.add_argument(
        '-s', '--style',
        choices=['strip', 'transparent', 'border', 'blur'],
        default=DEFAULT_STYLE,
        help='边框样式: strip(白底条形), transparent(半透明), border(纯色边框), blur(模糊边框)',
    )
    parser.add_argument(
        '-t', '--text',
        default='',
        help='自定义水印文字（如摄影师名字）',
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

    args = parser.parse_args()

    # 检查输入路径
    # 如果args.input为None（config.py中DEFAULT_INPUT为空且未指定命令行参数），直接弹出选择
    # 如果args.input有值但路径不存在，也弹出选择
    input_path = None
    if args.input:
        input_path = Path(args.input)

    if input_path is None or not input_path.exists():
        try:
            import tkinter as tk
            from tkinter import filedialog

            # 创建隐藏的主窗口
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)

            if input_path and not input_path.exists():
                print(f"路径不存在: {args.input}")

            # 提示可以设置默认路径
            if not DEFAULT_INPUT:
                print("💡 提示: 在 config.py 中设置 DEFAULT_INPUT 可跳过此选择")
                print()

            # 询问用户选择文件还是文件夹
            print("请选择要处理的内容：")
            print("1. 选择单张图片")
            print("2. 选择文件夹（批量处理）")

            choice = input("请输入选择 (1/2): ").strip()

            if choice == '1':
                # 选择单张图片
                filetypes = [
                    ("图片文件", "*.jpg *.jpeg *.png *.tiff *.bmp"),
                    ("所有文件", "*.*")
                ]
                selected = filedialog.askopenfilename(
                    title="选择要处理的图片",
                    filetypes=filetypes
                )
                if selected:
                    input_path = Path(selected)
                else:
                    print("未选择文件，程序退出")
                    sys.exit(0)
            else:
                # 选择文件夹
                selected = filedialog.askdirectory(title="选择要处理的文件夹")
                if selected:
                    input_path = Path(selected)
                else:
                    print("未选择文件夹，程序退出")
                    sys.exit(0)

            root.destroy()
            print(f"已选择: {input_path}")

        except ImportError:
            print(f"错误: 未指定输入路径，请通过命令行参数或config.py设置DEFAULT_INPUT")
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

    # 显示当前使用的配置
    print(f"📌 当前配置:")
    print(f"   样式: {args.style}")
    print(f"   输入: {input_path}")
    if args.text:
        print(f"   自定义文字: {args.text}")
    print()

    # 判断是单张图片还是文件夹
    if input_path.is_file():
        # 单张图片处理
        if args.output:
            output_path = args.output
        else:
            output_path = str(input_path.parent / f"{input_path.stem}_{args.style}_watermark{input_path.suffix}")

        print(f"处理图片: {input_path.name}")
        if process_single_image(
            str(input_path), output_path, args.style, args.text, **kwargs
        ):
            print(f"✅ 完成! 保存到: {output_path}")
        else:
            print("❌ 处理失败!")
            sys.exit(1)

    elif input_path.is_dir():
        # 批量处理
        if args.output:
            output_dir = args.output
        else:
            output_dir = str(input_path / "watermark_output")

        print(f"批量处理文件夹: {input_path}")
        print(f"输出目录: {output_dir}")
        print("-" * 50)

        success, total = batch_process(
            str(input_path), output_dir, args.style, args.text, **kwargs
        )

        print("-" * 50)
        if success == total:
            print(f"✅ 全部完成: {success}/{total} 张图片处理成功")
        else:
            print(f"⚠️  部分完成: {success}/{total} 张图片处理成功")
        print(f"📁 输出目录: {output_dir}")

    else:
        print(f"错误: 无效的输入路径 - {args.input}")
        sys.exit(1)

    # exe模式下等待用户确认，源码运行时直接退出
    if getattr(sys, 'frozen', False):
        print()
        print("=" * 50)
        print("处理完成！")
        print("=" * 50)
        input("按回车键关闭...")


if __name__ == '__main__':
    main()
