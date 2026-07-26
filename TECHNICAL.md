# Photo Watermark 技术文档

## 目录

- [架构概述](#架构概述)
- [模块说明](#模块说明)
- [核心函数](#核心函数)
- [配置系统](#配置系统)
- [数据流](#数据流)
- [打包说明](#打包说明)
- [扩展指南](#扩展指南)

---

## 架构概述

```
┌─────────────────────────────────────────────────────────┐
│                      main() 入口                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │ argparse    │  │ tkinter     │  │ 参数解析     │     │
│  │ 命令行解析   │  │ 文件选择    │  │ 颜色/路径    │     │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘     │
│         └────────────────┼────────────────┘             │
│                          ▼                               │
│              process_single_image()                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │ read_exif() │  │ auto_rotate │  │ get_logo()  │     │
│  │ EXIF读取    │  │ 图片旋转    │  │ Logo匹配    │     │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘     │
│         └────────────────┼────────────────┘             │
│                          ▼                               │
│           ┌──────────────────────────┐                   │
│           │    水印样式分发           │                   │
│           └─────────┬────────────────┘                   │
│     ┌───────────┬───┴────┬───────────┐                   │
│     ▼           ▼        ▼           ▼                   │
│ ┌──────────┐ ┌────────┐ ┌────────┐ ┌────────┐          │
│ │  strip   │ │transpa-│ │ border │ │  blur  │          │
│ │ 白底边框  │ │rent半透明│ │纯色边框│ │模糊边框 │          │
│ └──────────┘ └────────┘ └────────┘ └────────┘          │
│                          ▼                               │
│                   Image.save() 输出                       │
└─────────────────────────────────────────────────────────┘
```

---

## 模块说明

### 文件结构

| 文件 | 用途 | 行数 |
|------|------|------|
| `photo_watermark.py` | 主程序，包含所有业务逻辑 | ~1560 |
| `config.py` | 配置文件，所有可调参数 | ~120 |
| `logos/*.png/jpeg` | 品牌Logo文件 | - |
| `pkg/setup.py` | 打包配置 | ~40 |
| `pkg/pyproject.toml` | 打包元数据 | ~50 |

### 依赖关系

```
photo_watermark.py
├── config.py          (from config import *)
├── PIL               (Image, ImageDraw, ImageFont)
├── exifread          (EXIF元数据读取)
├── argparse          (命令行解析)
├── tkinter           (可选，文件选择对话框)
└── pathlib           (路径处理)
```

---

## 核心函数

### 1. `read_exif(image_path: str) -> Dict`

读取照片的EXIF元数据。

**返回值：**
```python
{
    'camera': 'NIKON Z 7',           # 相机型号
    'lens': 'NIKKOR Z 24-120mm f/4', # 镜头型号
    'aperture': 'F5.6',              # 光圈
    'shutter': '1/125s',             # 快门速度
    'iso': 'ISO1100',                # ISO
    'focal_length': '120mm',         # 焦距
    'datetime': '2026:07:26',        # 拍摄时间
    'orientation': 1,                # EXIF方向（1-8）
    'brand': 'NIKON'                 # 品牌（用于Logo匹配）
}
```

**关键逻辑：**
- 品牌名称清理：去掉 `CORPORATION`、`CORP.` 等冗余词
- 光圈格式化：整数不显示小数点（`F8` 而非 `F8.0`）
- 时间处理：使用文件创建时间，格式化为 `YYYY:MM:DD`

### 2. `get_logo_by_brand(brand: str, logo_dir: str = '') -> str`

根据品牌自动匹配Logo文件。

**品牌映射表：**
```python
brand_logo_map = {
    'NIKON': 'nikon_logo.png',
    'CANON': 'canon_logo.png',
    'SONY': 'sony_logo.png',
    'FUJI': 'fuji_logo.png',
    'HASSELBLAD': 'hasselblad_logo.png',
    'OLYMPUS': 'olympus_logo.png',
    'OM DIGITAL': 'olympus_logo.png',  # OM System（前身奥林巴斯）
    'OM SYSTEM': 'olympus_logo.png',
    'PENTAX': 'pentax_logo.png',
    'RICOH': 'pentax_logo.png',        # 宾得已被理光收购
    'PANASONIC': 'panasonic_logo.png',
}
```

**匹配逻辑：**
1. 清理品牌名称（大写、去空格）
2. 遍历映射表，检查品牌名是否包含关键字
3. 返回第一个存在的Logo文件路径
4. 未匹配则返回默认 Nikon Logo

### 3. `auto_rotate_image(image: Image, orientation: int) -> Image`

根据EXIF Orientation值旋转图片。

| Orientation | 操作 |
|-------------|------|
| 1 | 正常（不旋转） |
| 2 | 水平翻转 |
| 3 | 旋转180° |
| 4 | 垂直翻转 |
| 5 | 顺时针90° + 水平翻转 |
| 6 | 顺时针270°（逆时针90°） |
| 7 | 逆时针90° + 水平翻转 |
| 8 | 顺时针90° |

### 4. `apply_white_border(image, exif_data, custom_text, logo_path)`

白底黑字边框样式（strip 样式，尼康/佳能风格）。

**布局：**
```
┌────────────────────────────────────────────┐
│                原始照片区域                  │
├────────────────────────────────────────────┤
│ NIKON Z 7           [Logo] 120mm F5.6 1/125 ISO1100 │
│ NIKKOR Z 24-120mm f/4 S              2026-07-25     │
└────────────────────────────────────────────┘
```

**参数来源：**
- 边框高度：`BORDER_HEIGHT_RATIO`
- 左侧边距：`LEFT_MARGIN_RATIO`
- 右侧边距：`RIGHT_MARGIN_RATIO`
- Logo和参数间距：`LOGO_PARAMS_SPACING_RATIO`
- 文字颜色：`COLOR_CAMERA`、`COLOR_LENS`、`COLOR_PARAMS`、`COLOR_DATE`
- 边框背景：`COLOR_BORDER`

### 5. `apply_transparent_watermark(image, text, position, opacity, font_ratio, text_color, margin_ratio)`

半透明水印样式。

**特点：**
- 创建 RGBA 透明图层
- 绘制阴影效果（偏移1-2像素）
- 合并图层后转回 RGB

**位置计算：**
```python
positions = {
    'top-left': (margin, margin),
    'top-right': (width - text_width - margin, margin),
    'bottom-left': (margin, height - text_height - margin),
    'bottom-right': (width - text_width - margin, height - text_height - margin),
}
```

### 6. `apply_color_border(image, exif_data, custom_text, border_color, text_color, border_side_ratio, border_bottom_ratio)`

纯色边框样式。

**布局：**
```
┌──────────────────────────────────────────┐
│  ┌──────────────────────────────────┐    │
│  │                                  │    │
│  │         原始照片区域              │    │
│  │                                  │    │
│  └──────────────────────────────────┘    │
│          拍摄参数 | 自定义文字            │
└──────────────────────────────────────────┘
```

**参数：**
- 左右边框：`BORDER_SIDE_RATIO`（默认4%）
- 底部边框：`BORDER_BOTTOM_RATIO`（默认8%，比两侧宽）
- 文字居中显示

---

## 配置系统

### 配置加载优先级

```
命令行参数 > config.py > 代码默认值
```

**实现方式：**
```python
# photo_watermark.py
try:
    from config import *      # 优先加载 config.py
except ImportError:
    # config.py 不存在时使用代码默认值
    DEFAULT_STYLE = 'strip'
    ...
```

### 参数传递机制

```
main() 
  ├── args = parser.parse_args()    # 解析命令行
  ├── kwargs = {                    # 组装参数字典
  │     'position': args.position,
  │     'border_color': border_color,
  │     'text_color': text_color,
  │     ...
  │   }
  └── process_single_image(**kwargs)
        └── apply_xxx(..., **kwargs)
              └── 使用 config 默认值或 kwargs 覆盖
```

**None 值处理：**
```python
# 命令行不指定时，argparse 默认为 None
parser.add_argument('--border-color', default=None)

# 传入函数时，None 表示使用 config 默认值
def apply_color_border(border_color=None):
    if border_color is None:
        border_color = BORDER_FRAME_COLOR  # 来自 config.py
```

### 配置项分类

| 分类 | 配置项 | 说明 |
|------|--------|------|
| **通用** | `DEFAULT_STYLE` | 默认水印样式 |
| | `DEFAULT_INPUT` | 默认输入路径（空=弹窗） |
| | `DEFAULT_OUTPUT` | 默认输出路径（空=自动） |
| | `JPEG_QUALITY` | JPEG 质量 |
| | `FONT_SIZE_RATIO` | 字体大小比例 |
| **strip** | `BORDER_HEIGHT_RATIO` | 边框高度 |
| | `LEFT_MARGIN_RATIO` | 左侧边距 |
| | `RIGHT_MARGIN_RATIO` | 右侧边距 |
| | `LOGO_HEIGHT_RATIO` | Logo 高度 |
| | `COLOR_*` | 各种颜色 |
| **transparent** | `TRANSPARENT_POSITION` | 水印位置 |
| | `TRANSPARENT_OPACITY` | 透明度 |
| | `TRANSPARENT_FONT_RATIO` | 字体大小 |
| | `TRANSPARENT_TEXT_COLOR` | 文字颜色 |
| **border** | `BORDER_FRAME_COLOR` | 边框颜色 |
| | `BORDER_TEXT_COLOR` | 文字颜色 |
| | `BORDER_SIDE_RATIO` | 左右边框宽度 |
| | `BORDER_BOTTOM_RATIO` | 底部边框宽度 |

---

## 数据流

### 单张图片处理流程

```
输入图片路径
    │
    ▼
Image.open() ──────────────────────────┐
    │                                  │
    ▼                                  │
read_exif() ──► exif_data              │
    │           ├── camera             │
    │           ├── lens               │
    │           ├── brand ─────────────┼──► get_logo_by_brand()
    │           ├── aperture           │         │
    │           ├── shutter            │         ▼
    │           ├── iso                │    logo_path
    │           ├── focal_length       │
    │           └── orientation ───────┼──► auto_rotate_image()
    │                                  │         │
    ▼                                  │         ▼
┌──────────────────────────────────────┼─────────┘
│         样式分发                      │
├──────────┬───────────┬──────────┬────────┤
│ strip    │transparent│ border   │ blur   │
│          │           │               │
│ 左侧:    │ 位置计算   │ 四周边框      │
│ 相机+镜头 │ 阴影绘制   │ 底部文字      │
│ 右侧:    │ 文字绘制   │               │
│ Logo+参数 │ 图层合并   │               │
│ 底部:日期 │           │               │
├──────────┴───────────┴───────────────┤
│                  │                    │
│                  ▼                    │
│           result.save()              │
│                  │                    │
│                  ▼                    │
│            输出文件路径                │
└──────────────────────────────────────┘
```

### 批量处理流程

```
输入文件夹
    │
    ▼
glob("*.{jpg,jpeg,png,tiff,bmp}")
    │
    ▼
去重 + 排序
    │
    ▼
for img_file in image_files:
    │
    ├── 生成输出文件名: {stem}_watermark{suffix}
    │
    └── process_single_image()
            │
            ▼
        统计 success_count / total
            │
            ▼
        返回 (成功数, 总数)
```

---

## 打包说明

### 使用 PyInstaller 打包

```bash
# 安装 PyInstaller
pip install pyinstaller

# 打包为单文件 exe
pyinstaller --onefile photo_watermark.py

# 打包后需要手动复制：
# - config.py
# - logos/ 文件夹
```

### 打包配置（pkg/pyproject.toml）

```toml
[project]
name = "photo-watermark"
version = "1.1.0"
dependencies = ["Pillow>=10.0.0", "exifread>=3.0.0"]

[project.scripts]
photo-watermark = "photo_watermark:main"
```

### exe 使用注意事项

exe 用户需要将以下文件放在 exe 同目录：
- `config.py`
- `logos/` 文件夹（含所有 Logo 文件）

---

## 扩展指南

### 添加新品牌 Logo

1. **添加 Logo 文件**
   ```bash
   # 放入 logos/ 文件夹，命名规范：{brand}_logo.{png|jpeg}
   cp new_brand_logo.png logos/newbrand_logo.png
   ```

2. **更新品牌映射**
   ```python
   # photo_watermark.py → get_logo_by_brand()
   brand_logo_map = {
       ...
       'NEWBRAND': 'newbrand_logo.png',
   }
   ```

3. **更新 README**（品牌表格）

### 添加新水印样式

1. **在 config.py 添加配置项**
   ```python
   # ==================== new_style 样式 ====================
   NEW_STYLE_PARAM1 = value1
   NEW_STYLE_PARAM2 = value2
   ```

2. **在 photo_watermark.py 添加函数**
   ```python
   def apply_new_style(image, exif_data, custom_text='', **kwargs):
       # 实现新样式
       return result_image
   ```

3. **在 process_single_image() 添加分支**
   ```python
   elif style == 'new_style':
       result = apply_new_style(image, exif_data, custom_text, **kwargs)
   ```

4. **在 argparse 添加选项**
   ```python
   parser.add_argument('-s', '--style', choices=['strip', 'transparent', 'border', 'blur', 'new_style'])
   ```

5. **更新 config.py 的 DEFAULT_STYLE 注释**

### 修改字体

```python
# photo_watermark.py → get_font()
font_paths = [
    'C:/Windows/Fonts/msyh.ttc',      # 微软雅黑
    'C:/Windows/Fonts/simhei.ttf',     # 黑体
    'C:/Windows/Fonts/simsun.ttc',     # 宋体
    'C:/Windows/Fonts/arial.ttf',      # Arial
]
```

优先级：从上到下，第一个成功加载的字体被使用。

### 修改日期格式

```python
# photo_watermark.py → read_exif()
# 当前逻辑：优先 EXIF 拍摄时间，回退到文件创建时间
for tag in ['EXIF DateTimeOriginal', 'EXIF DateTimeDigitized', 'Image DateTime']:
    if tag in tags:
        exif_data['datetime'] = str(tags[tag]).strip()
        break
if not exif_data['datetime']:
    ctime = os.path.getctime(image_path)
    exif_data['datetime'] = datetime.fromtimestamp(ctime).strftime('%Y:%m:%d %H:%M:%S')
```

---

## 已知限制

1. **字体依赖系统** — 需要 Windows 系统字体，Linux/Mac 需修改字体路径
2. **Logo 格式** — 支持 PNG 和 JPEG，PNG 需带 Alpha 通道才能透明
3. **EXIF 读取** — 部分照片可能缺少 EXIF 信息，相关字段留空
4. **内存占用** — 大尺寸照片处理时内存占用较高
5. **日期来源** — 优先使用 EXIF 拍摄时间，缺失时回退到文件创建时间

---

## 性能优化建议

1. **批量处理** — 使用 `--input` 指定文件夹，避免逐张处理
2. **JPEG 质量** — 非必要不使用 100，95 足够且文件更小
3. **图片预处理** — 如需处理大量照片，可先缩小再添加水印
4. **Logo 缓存** — Logo 文件在每次处理时重新加载，可考虑缓存

---

*文档版本：v1.4.0*
*最后更新：2026-07-26*
