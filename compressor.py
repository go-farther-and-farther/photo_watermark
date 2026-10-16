# -*- coding: utf-8 -*-
"""
压缩/转换核心模块（「无水印(仅压缩)」样式的后端）

只依赖 Pillow，不依赖 Tkinter，可独立单测。
多数逻辑移植自 image_compress 项目（encode_jpeg/write_atomic/RGBA白底合成/元数据sidecar）。
"""

import json
import os
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

# 「最大边长」设置的可选档位（显示名 -> 像素；0=保持原尺寸）
MAX_EDGE_PRESETS = [
    (0, '保持原尺寸'),
    (7680, '8K (7680)'),
    (3840, '4K (3840)'),
    (2560, '2K (2560)'),
]

# 目标体积模式的质量搜索范围
TARGET_QUALITY_MIN = 30
TARGET_QUALITY_MAX = 95


def resize_image(img: Image.Image, max_edge: int) -> Image.Image:
    """等比缩放到最大边长不超过 max_edge；只缩不放，max_edge<=0 原样返回"""
    if not max_edge or max_edge <= 0:
        return img
    w, h = img.size
    edge = max(w, h)
    if edge <= max_edge:
        return img
    scale = max_edge / edge
    new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return img.resize(new_size, Image.LANCZOS)


def flatten_to_rgb(img: Image.Image) -> Image.Image:
    """
    转成 RGB 模式；带透明的模式（RGBA/LA/带透明表的 P）先合成白底，
    避免 JPEG 丢弃 alpha 后透明区域变黑。其余模式直接 convert。
    """
    if img.mode == 'RGB':
        return img
    if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
        rgba = img.convert('RGBA')
        bg = Image.new('RGB', rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel('A'))
        return bg
    return img.convert('RGB')


def encode_jpeg_bytes(img: Image.Image, quality: int,
                      exif: bytes = b'', icc=None) -> bytes:
    """编码 JPEG 到内存：先知道大小再决定落盘，避免越压越大还覆盖"""
    buf = BytesIO()
    kwargs = {'quality': quality, 'optimize': True}
    if exif:
        kwargs['exif'] = exif
    if icc:
        kwargs['icc_profile'] = icc
    img.save(buf, 'JPEG', **kwargs)
    return buf.getvalue()


def encode_to_target_size(img: Image.Image, target_mb: float,
                          exif: bytes = b'', icc=None) -> Tuple[bytes, int, bool]:
    """
    目标体积模式：二分搜索质量使文件不超过 target_mb。

    Returns:
        (JPEG 数据, 实际使用的质量, 是否达标)；达标取满足目标的最高质量，
        质量下限仍超标时按该下限输出并返回 False（由调用方提示）。
    """
    limit = target_mb * 1024 * 1024
    if limit <= 0:
        return encode_jpeg_bytes(img, TARGET_QUALITY_MAX, exif, icc), TARGET_QUALITY_MAX, True

    def enc(q):
        return encode_jpeg_bytes(img, q, exif, icc)

    data = enc(TARGET_QUALITY_MAX)
    if len(data) <= limit:
        return data, TARGET_QUALITY_MAX, True

    best = None
    lo, hi = TARGET_QUALITY_MIN, TARGET_QUALITY_MAX - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        d = enc(mid)
        if len(d) <= limit:
            best = (d, mid, True)
            lo = mid + 1
        else:
            hi = mid - 1
    if best:
        return best
    data = enc(TARGET_QUALITY_MIN)
    return data, TARGET_QUALITY_MIN, False


def write_atomic(data: bytes, out_path) -> None:
    """先写同目录临时文件、成功后原子替换；中途失败不损坏目标文件"""
    tmp_path = Path(str(out_path) + '.tmp')
    try:
        with open(tmp_path, 'wb') as tf:
            tf.write(data)
        os.replace(str(tmp_path), str(out_path))
    finally:
        tmp_path.unlink(missing_ok=True)


_ILLEGAL_CHARS = set('\\/:*?"<>|')


def sanitize_subfolder(name: str) -> str:
    """清洗子文件夹名：去 Windows 非法字符与首尾空白/点号；空或非法回退 JPEG"""
    cleaned = ''.join(ch for ch in str(name or '') if ch not in _ILLEGAL_CHARS)
    cleaned = cleaned.strip().strip('.').strip()
    return cleaned or 'JPEG'


def save_metadata_sidecar(info: dict, out_dir, stem: str) -> Optional[Path]:
    """
    把源 PNG 的字符串元数据（ComfyUI/SD 工作流等）保存为输出目录下的 <stem>.json。
    已存在时不覆盖。返回写入的路径，无需保存时返回 None。
    """
    metadata = {}
    for k, v in (info or {}).items():
        if isinstance(v, str):
            try:
                metadata[k] = json.loads(v)
            except (json.JSONDecodeError, ValueError):
                metadata[k] = v
    if not metadata:
        return None
    json_path = Path(out_dir) / f"{stem}.json"
    if json_path.exists():
        return None
    with open(json_path, 'w', encoding='utf-8') as jf:
        json.dump(metadata, jf, ensure_ascii=False, indent=2)
    return json_path
