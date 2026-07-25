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
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont
import exifread

# 导入配置
try:
    from config import *
except ImportError:
    # 默认配置（如果config.py不存在）
    BORDER_HEIGHT_RATIO = 0.08
    LEFT_MARGIN_RATIO = 0.025
    LINE_SPACING = 6
    RIGHT_MARGIN_RATIO = 0.025
    LOGO_PARAMS_SPACING_RATIO = 0.03
    LOGO_HEIGHT_RATIO = 0.7
    VERTICAL_OFFSET_RATIO = 0.15
    FONT_SIZE_RATIO = 3
    COLOR_CAMERA = (30, 30, 30)
    COLOR_LENS = (120, 120, 120)
    COLOR_PARAMS = (30, 30, 30)
    COLOR_DATE = (100, 100, 100)
    COLOR_BORDER = (255, 255, 255)


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
            except:
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

        # 拍摄时间 - 使用文件创建日期
        import os
        from datetime import datetime
        # 获取文件创建时间
        ctime = os.path.getctime(image_path)
        exif_data['datetime'] = datetime.fromtimestamp(ctime).strftime('%Y:%m:%d %H:%M:%S')

    except Exception as e:
        print(f"警告: 读取EXIF信息失败 - {e}")

    return exif_data


def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """
    获取字体，优先使用系统字体

    Args:
        size: 字体大小
        bold: 是否使用粗体

    Returns:
        字体对象
    """
    # Windows系统字体路径
    font_paths = [
        'C:/Windows/Fonts/msyh.ttc',      # 微软雅黑
        'C:/Windows/Fonts/simhei.ttf',     # 黑体
        'C:/Windows/Fonts/simsun.ttc',     # 宋体
        'C:/Windows/Fonts/arial.ttf',      # Arial
    ]

    for font_path in font_paths:
        try:
            return ImageFont.truetype(font_path, size)
        except:
            continue

    # 如果都失败，使用默认字体
    return ImageFont.load_default()


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
    params = []
    if exif_data.get('focal_length'):
        params.append(exif_data['focal_length'])
    if exif_data.get('aperture'):
        params.append(exif_data['aperture'])
    if exif_data.get('shutter'):
        params.append(exif_data['shutter'])
    if exif_data.get('iso'):
        params.append(exif_data['iso'])

    if params:
        parts.append(' | '.join(params))

    # 拍摄时间
    if show_all and exif_data.get('datetime'):
        parts.append(exif_data['datetime'])

    return '  •  '.join(parts) if parts else ''


def apply_white_border(
    image: Image.Image,
    exif_data: Dict[str, str],
    custom_text: str = '',
    border_ratio: float = 0.08,
    logo_path: str = '',
) -> Image.Image:
    """
    应用白底黑字边框样式（尼康/佳能风格）

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
    params_parts = []
    if exif_data.get('focal_length'):
        params_parts.append(exif_data['focal_length'])
    if exif_data.get('aperture'):
        params_parts.append(exif_data['aperture'])
    if exif_data.get('shutter'):
        params_parts.append(exif_data['shutter'])
    if exif_data.get('iso'):
        params_parts.append(exif_data['iso'])

    params_text = ' '.join(params_parts) if params_parts else ''

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
            # 调整logo大小
            logo_height = int(border_height * LOGO_HEIGHT_RATIO)
            logo_ratio = logo_height / logo.height
            logo_width = int(logo.width * logo_ratio)
            logo = logo.resize((logo_width, logo_height), Image.Resampling.LANCZOS)

            # Logo位置（参数左侧）
            logo_x = params_x - logo_width - logo_params_spacing
            logo_y = height + (border_height - logo_height) // 2
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
    position: str = 'bottom-right',
    opacity: int = 128,
    font_ratio: float = 0.03,
) -> Image.Image:
    """
    应用半透明水印样式

    Args:
        image: 原始图片
        text: 水印文字
        position: 位置（top-left, top-right, bottom-left, bottom-right）
        opacity: 透明度（0-255）
        font_ratio: 字体大小比例（相对于图片宽度）

    Returns:
        添加水印后的图片
    """
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
    margin = int(width * 0.02)
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
    draw.text((x, y), text, fill=(255, 255, 255, opacity), font=font)

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
    border_color: Tuple[int, int, int] = (0, 0, 0),
    text_color: Tuple[int, int, int] = (255, 255, 255),
    border_ratio: float = 0.04,
) -> Image.Image:
    """
    应用纯色边框+文字样式

    Args:
        image: 原始图片
        exif_data: EXIF数据
        custom_text: 自定义文字
        border_color: 边框颜色
        text_color: 文字颜色
        border_ratio: 边框宽度比例

    Returns:
        添加边框后的图片
    """
    width, height = image.size
    border_size = int(width * border_ratio)
    bottom_border = int(width * 0.08)  # 底部边框更宽

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


def process_single_image(
    input_path: str,
    output_path: str,
    style: str = 'white',
    custom_text: str = '',
    **kwargs,
) -> bool:
    """
    处理单张图片

    Args:
        input_path: 输入图片路径
        output_path: 输出图片路径
        style: 边框样式（white, transparent, border）
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

        # 根据样式应用水印
        if style == 'white':
            result = apply_white_border(
                image, exif_data, custom_text,
                border_ratio=kwargs.get('border_ratio', 0.06),
                logo_path=kwargs.get('logo_path', ''),
            )
        elif style == 'transparent':
            result = apply_transparent_watermark(
                image, custom_text or format_exif_text(exif_data),
                position=kwargs.get('position', 'bottom-right'),
                opacity=kwargs.get('opacity', 128),
                font_ratio=kwargs.get('font_ratio', 0.03),
            )
        elif style == 'border':
            border_color = kwargs.get('border_color', (0, 0, 0))
            text_color = kwargs.get('text_color', (255, 255, 255))
            result = apply_color_border(
                image, exif_data, custom_text,
                border_color=border_color,
                text_color=text_color,
                border_ratio=kwargs.get('border_ratio', 0.04),
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
            result.save(output_path, 'JPEG', quality=kwargs.get('quality', 95))
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
    style: str = 'white',
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
        # 生成输出文件名
        output_file = output_path / f"{img_file.stem}_watermark{img_file.suffix}"

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
    except:
        pass

    print(f"警告: 无法解析颜色 '{color_str}'，使用默认黑色")
    return (0, 0, 0)


def main():
    # 显示项目信息
    print("=" * 50)
    print("  Photo Watermark - 相机照片水印边框生成器")
    print("  版本: v1.0.1")
    print("  项目: https://github.com/go-farther-and-farther/photo_watermark")
    print("=" * 50)
    print()

    parser = argparse.ArgumentParser(
        description='相机照片水印边框生成器',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 白底黑字边框样式
  python photo_watermark.py input.jpg --style white --text "©摄影师"

  # 半透明水印
  python photo_watermark.py input.jpg --style transparent --position bottom-right

  # 纯色边框样式
  python photo_watermark.py input.jpg --style border --border-color black --text "©摄影师"

  # 批量处理
  python photo_watermark.py ./photos/ --style white --text "©摄影师" --output ./output/
        """,
    )

    parser.add_argument(
        'input',
        nargs='?',
        default='./input',
        help='输入图片路径或文件夹路径（默认: ./input）',
    )
    parser.add_argument(
        '-o', '--output',
        default='./output',
        help='输出路径（默认: ./output）',
    )
    parser.add_argument(
        '-s', '--style',
        choices=['white', 'transparent', 'border'],
        default='white',
        help='边框样式: white(白底黑字), transparent(半透明), border(纯色边框)',
    )
    parser.add_argument(
        '-t', '--text',
        default='',
        help='自定义水印文字（如摄影师名字）',
    )
    parser.add_argument(
        '-p', '--position',
        choices=['top-left', 'top-right', 'bottom-left', 'bottom-right'],
        default='bottom-right',
        help='半透明水印的位置',
    )
    parser.add_argument(
        '--border-color',
        default='black',
        help='边框颜色（black/white/gray或RGB格式如255,255,255）',
    )
    parser.add_argument(
        '--text-color',
        default='white',
        help='文字颜色',
    )
    parser.add_argument(
        '--opacity',
        type=int,
        default=128,
        help='半透明水印的透明度（0-255）',
    )
    parser.add_argument(
        '--quality',
        type=int,
        default=95,
        help='JPEG输出质量（1-100）',
    )
    parser.add_argument(
        '--logo',
        default=str(Path(__file__).parent / 'nikon_logo.png'),
        help='Logo图片路径（默认: nikon_logo.png）',
    )

    args = parser.parse_args()

    # 检查输入路径，不存在时弹出文件夹选择对话框
    input_path = Path(args.input)
    if not input_path.exists():
        try:
            import tkinter as tk
            from tkinter import filedialog

            # 创建隐藏的主窗口
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)

            # 询问用户选择文件还是文件夹
            print(f"路径不存在: {args.input}")
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
            print(f"错误: 输入路径不存在 - {args.input}")
            sys.exit(1)

    # 解析颜色
    border_color = parse_color(args.border_color)
    text_color = parse_color(args.text_color)

    # 准备参数
    kwargs = {
        'position': args.position,
        'border_color': border_color,
        'text_color': text_color,
        'opacity': args.opacity,
        'quality': args.quality,
        'logo_path': args.logo,
    }

    # 判断是单张图片还是文件夹
    if input_path.is_file():
        # 单张图片处理
        if args.output:
            output_path = args.output
        else:
            output_path = str(input_path.parent / f"{input_path.stem}_watermark{input_path.suffix}")

        print(f"处理图片: {input_path.name}")
        if process_single_image(
            str(input_path), output_path, args.style, args.text, **kwargs
        ):
            print(f"完成! 保存到: {output_path}")
        else:
            print("处理失败!")
            sys.exit(1)

    elif input_path.is_dir():
        # 批量处理
        if args.output:
            output_dir = args.output
        else:
            output_dir = str(input_path / "watermark_output")

        print(f"批量处理文件夹: {input_path}")
        print(f"输出目录: {output_dir}")
        print(f"样式: {args.style}")
        print("-" * 50)

        success, total = batch_process(
            str(input_path), output_dir, args.style, args.text, **kwargs
        )

        print("-" * 50)
        print(f"处理完成: {success}/{total} 张图片成功")
        print(f"输出目录: {output_dir}")

    else:
        print(f"错误: 无效的输入路径 - {args.input}")
        sys.exit(1)


if __name__ == '__main__':
    main()
