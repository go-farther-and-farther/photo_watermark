# -*- coding: utf-8 -*-
"""照片调色模板（简单调色）：线性光 8 参数 + 模板读写

设计见 docs/superpowers/specs/2026-10-18-color-grade-presets-design.md

为什么不用 `_lut` 里那条 .cube：它是「N-Log 进、Rec.709 出」的视频技术转换，
照片是 sRGB，直接套是双重编码（实测中灰 0.18 → 0.05，暗 1.8 档）。这里把它的
stage-2 look（黑点/曝光/高光肩/门控色度）搬到线性光上，黑点按照片自适应。

管线（全部线性光，8bit sRGB 进出）：
    sRGB 解码 -> 白平衡 -> 曝光 -> 黑点 -> 对比 -> 高光肩 -> 阴影 -> 门控饱和 -> 编码
强度 = 参数朝恒等值缩放后只跑一遍（不做两次重采样）。
"""
from __future__ import annotations

import configparser
from pathlib import Path

import numpy as np

LUM = np.array([0.2126, 0.7152, 0.0722], np.float32)

# 参数定义：(ini 键, 内部名, 恒等值, 下限, 上限)
PARAM_DEFS = (
    ('曝光', 'exposure', 0.0, -2.0, 2.0),
    ('对比', 'contrast', 1.0, 0.5, 1.5),
    ('高光', 'highlights', 0.0, 0.0, 0.30),
    ('阴影', 'shadows', 0.0, 0.0, 0.05),
    ('饱和', 'saturation', 1.0, 0.0, 1.6),
    ('色温', 'temp', 0.0, -1.0, 1.0),
    ('色调', 'tint', 0.0, -1.0, 1.0),
    ('黑点', 'black', 0.0, 0.0, 0.02),
)
IDENTITY = {name: ident for _k, name, ident, _lo, _hi in PARAM_DEFS}
NAME_OF = {name: key for key, name, _i, _lo, _hi in PARAM_DEFS}   # 内部名 -> ini 键

SECTION_PREFIX = '调色模板:'
OFF_TEMPLATE = '原图'
BLACK_AUTO_MAX = 0.012      # auto 黑点上限（视频那条 look 是 0.012，照片通常更小）
HIGHLIGHT_KNEE = 0.75
HIGHLIGHT_Q = 3.0
CHROMA_GATE = 0.06          # 低色度区不参与饱和，保护肤色/灰面

# 内置模板（随包分发，用户段同名可覆盖）
BUILTIN_TEMPLATES = {
    '标准': dict(exposure=0.00, contrast=1.10, highlights=0.04, shadows=0.0,
               saturation=1.08, temp=0.0, tint=0.0, black=0.0, black_auto=True),
    # 视频感 = _lut/N-Log_to_Rec709_Standard.cube 的 look 移植（黑点自适应）
    '视频感': dict(exposure=-0.15, contrast=1.00, highlights=0.12, shadows=0.0,
                saturation=1.30, temp=0.0, tint=0.0, black=0.0, black_auto=True),
    '清新': dict(exposure=0.15, contrast=1.05, highlights=0.02, shadows=0.010,
               saturation=1.10, temp=-0.30, tint=0.0, black=0.0, black_auto=True),
    '暖调': dict(exposure=0.05, contrast=1.12, highlights=0.06, shadows=0.0,
               saturation=1.15, temp=0.35, tint=0.0, black=0.0, black_auto=True),
    '黑白': dict(exposure=0.0, contrast=1.15, highlights=0.03, shadows=0.0,
               saturation=0.0, temp=0.0, tint=0.0, black=0.0, black_auto=False),
}


# ---------- 颜色数学 ----------

def srgb_to_linear(v):
    v = np.asarray(v, np.float32)
    return np.where(v <= 0.04045, v / 12.92,
                    np.power((np.maximum(v, 0.0) + 0.055) / 1.055, 2.4)).astype(np.float32)


def linear_to_srgb(v):
    v = np.clip(np.asarray(v, np.float32), 0.0, 1.0)
    return np.where(v <= 0.0031308, 12.92 * v,
                    np.float32(1.055) * np.power(v, 1.0 / 2.4) - np.float32(0.055))


def _smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def auto_black(linear):
    """按照片线性亮度的 P0.5 定黑点（夹 0..BLACK_AUTO_MAX）"""
    y = linear @ LUM
    b = float(np.percentile(y, 0.5))
    return float(np.clip(b, 0.0, BLACK_AUTO_MAX))


def _prelinear(a01, params, t):
    """sRGB 0..1 -> 线性光，做完白平衡 + 曝光（黑点之前的步骤）

    抽出来是为了"按图定黑点"可以在降采样图上算一次、整图复用，而不必为
    每一块重算。
    """
    x = srgb_to_linear(a01)
    temp = params.get('temp', 0.0) * t
    tint = params.get('tint', 0.0) * t
    if temp or tint:
        g = np.array([2.0 ** (0.30 * temp), 2.0 ** (0.15 * tint),
                      2.0 ** (-0.30 * temp)], np.float32)
        x = x * g
    ev = params.get('exposure', 0.0) * t
    if ev:
        x = x * np.float32(2.0 ** ev)
    return x


def grade_array(rgb01, params, strength=1.0, black_auto_value=None):
    """rgb01: HxWx3 float32/uint8 的 0..1 sRGB。返回同形状 float32 sRGB。

    black_auto_value 给定时用它当"按图自动黑点"（整图算一次，分块时复用）。
    """
    a = np.asarray(rgb01)
    if a.dtype == np.uint8:
        a = a.astype(np.float32) / 255.0
    else:
        a = a.astype(np.float32)
    t = float(np.clip(strength, 0.0, 1.0))
    p = dict(IDENTITY)
    p.update({k: v for k, v in params.items() if k in IDENTITY})
    if t <= 0.0:
        return np.clip(a, 0, 1)
    c = 1.0 + (p['contrast'] - 1.0) * t
    hi = p['highlights'] * t
    sh = p['shadows'] * t
    sat = 1.0 + (p['saturation'] - 1.0) * t

    x = _prelinear(a, p, t)
    # 3) 黑点（auto 按本图定，切掉暗部底噪；白点保持 1）
    black = p['black'] * t
    if p.get('black_auto'):
        b = black_auto_value if black_auto_value is not None else auto_black(x)
        black = max(black, b * t)
    if black > 0:
        x = np.maximum(x - black, 0.0) / np.float32(max(1.0 - black, 1e-6))
    # 4) 对比：绕 0.18 的幂曲线
    if c != 1.0:
        x = np.power(np.maximum(x, 0.0), c) * np.float32(0.18 ** (1.0 - c))
    # 5) 高光肩
    if hi > 0:
        over = x > HIGHLIGHT_KNEE
        if over.any():
            d = x[over] - HIGHLIGHT_KNEE
            x[over] = HIGHLIGHT_KNEE + hi * d / (d + HIGHLIGHT_Q)
    x = np.clip(x, 0.0, 1.0)
    # 6) 阴影提亮（越暗提得越多）
    if sh > 0:
        x = x + sh * (1.0 - x) ** 2
    # 7) 门控饱和：低色度区不动（肤色/灰面不泛色）
    if sat != 1.0:
        y = x @ LUM
        mx = x.max(axis=2)
        mn = x.min(axis=2)
        k = 1.0 + (sat - 1.0) * _smoothstep((mx - mn) / CHROMA_GATE)
        x = y[..., None] + k[..., None] * (x - y[..., None])
    return linear_to_srgb(np.clip(x, 0.0, 1.0)).astype(np.float32)


def apply_grade(image, params, strength=1.0, band_rows=256):
    """PIL Image（RGB）-> 调色后的 PIL Image；强度 0 / params 恒等时原样返回

    大图分批（默认每 512 行）处理：整图一次性做线性化会为 45MP 照片开出
    3~4GB 的浮点临时数组（实测峰值 3.8GB / 8 秒）。调色是逐像素运算，
    分块结果与整图一致；"自动黑点"在 1/8 缩略图上算一次再复用。
    """
    from PIL import Image
    if strength <= 0 or not params:
        return image
    t = float(np.clip(strength, 0.0, 1.0))
    if t <= 0:
        return image
    p = dict(IDENTITY)
    p.update({k: v for k, v in params.items() if k in IDENTITY})
    src = image.convert('RGB')
    arr = np.asarray(src, np.uint8)
    h, w = arr.shape[:2]
    if h <= band_rows and w * h <= 4_000_000:
        out = grade_array(arr, p, t)
        return Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8), 'RGB')
    # 自动黑点：1/8 缩略图上算（BOX 平均，统计量与整图一致）
    black_auto = None
    if p.get('black_auto'):
        small = src.resize((max(1, w // 8), max(1, h // 8)), Image.BOX)
        s = np.asarray(small, np.uint8).astype(np.float32) / 255.0
        black_auto = auto_black(_prelinear(s, p, t))
    out = np.empty((h, w, 3), np.uint8)
    for y0 in range(0, h, band_rows):
        band = arr[y0:y0 + band_rows].astype(np.float32) / 255.0
        g = grade_array(band, p, t, black_auto_value=black_auto)
        out[y0:y0 + band_rows] = (np.clip(g, 0, 1) * 255 + 0.5).astype(np.uint8)
    return Image.fromarray(out, 'RGB')


# ---------- 模板读写 ----------

def _clean_name(name):
    return str(name).replace(']', '').replace('[', '').replace('\n', ' ').strip()


def parse_template_section(items):
    """configparser 段 items -> params dict（缺键取恒等，值 clamp，'auto' 支持）"""
    p = {
        'exposure': 0.0, 'contrast': 1.0, 'highlights': 0.0, 'shadows': 0.0,
        'saturation': 1.0, 'temp': 0.0, 'tint': 0.0, 'black': 0.0,
        'black_auto': False,
    }
    for key, name, ident, lo, hi in PARAM_DEFS:
        vals = [v for k, v in items if k == key]
        if not vals:
            continue
        raw = str(vals[-1]).strip()
        if name == 'black' and raw.lower() == 'auto':
            p['black_auto'] = True
            p['black'] = 0.0
            continue
        try:
            p[name] = float(np.clip(float(raw), lo, hi))
        except (TypeError, ValueError):
            p[name] = ident
    return p


def load_templates(ini_config):
    """内置 + ini 里的 [调色模板:*]（用户段覆盖同名内置）"""
    out = {k: dict(v) for k, v in BUILTIN_TEMPLATES.items()}
    for sec in ini_config.sections():
        if not sec.startswith(SECTION_PREFIX):
            continue
        name = _clean_name(sec[len(SECTION_PREFIX):])
        if not name:
            continue
        out[name] = parse_template_section(ini_config.items(sec))
    return out


def template_names(templates):
    """下拉框顺序：原图在最前，其余按名字排（以 _ 开头的是内部临时模板，不显示）"""
    rest = sorted(k for k in templates if k != OFF_TEMPLATE and not k.startswith('_'))
    return [OFF_TEMPLATE] + rest


def _user_ini_path(base_dir):
    return Path(base_dir) / '用户设置.ini'


def _write_user_sections(base_dir, mutate):
    """读 用户设置.ini -> mutate(cp) -> 写回（保留文件头注释）"""
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    path = _user_ini_path(base_dir)
    if path.exists():
        cp.read(path, encoding='utf-8')
    mutate(cp)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('# ==========================================\n')
        f.write('# 个人设置（由程序设置窗口自动生成，无需手动编辑）\n')
        f.write('# 此文件不会被程序更新覆盖，请放心使用\n')
        f.write('# ==========================================\n')
        cp.write(f)
    return True


def save_template(base_dir, name, params):
    """把参数写成 [调色模板:名字]（同名覆盖）到 用户设置.ini"""
    name = _clean_name(name)
    if not name:
        raise ValueError('模板名不能为空')
    if name == OFF_TEMPLATE:
        raise ValueError('「原图」是保留名，换一个名字')
    p = dict(IDENTITY)
    p.update({k: v for k, v in params.items() if k in IDENTITY})
    black = 'auto' if params.get('black_auto') else p['black']

    def mutate(cp):
        sec = SECTION_PREFIX + name
        if not cp.has_section(sec):
            cp.add_section(sec)
        cp.set(sec, '曝光', f"{p['exposure']:.3f}")
        cp.set(sec, '对比', f"{p['contrast']:.3f}")
        cp.set(sec, '高光', f"{p['highlights']:.3f}")
        cp.set(sec, '阴影', f"{p['shadows']:.4f}")
        cp.set(sec, '饱和', f"{p['saturation']:.3f}")
        cp.set(sec, '色温', f"{p['temp']:.3f}")
        cp.set(sec, '色调', f"{p['tint']:.3f}")
        cp.set(sec, '黑点', 'auto' if black == 'auto' else f"{float(black):.4f}")
    return _write_user_sections(base_dir, mutate)


def delete_template(base_dir, name):
    name = _clean_name(name)
    if not name or name == OFF_TEMPLATE:
        return False

    def mutate(cp):
        sec = SECTION_PREFIX + name
        if cp.has_section(sec):
            cp.remove_section(sec)
    _write_user_sections(base_dir, mutate)
    return True


def is_builtin(name):
    return _clean_name(name) in BUILTIN_TEMPLATES
