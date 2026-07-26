# Photo Watermark - 相机照片水印边框生成器

给相机照片添加水印边框，显示拍摄参数（EXIF信息），支持批量处理。

## 功能特点

- **自动读取EXIF拍摄参数**
  - 相机型号、镜头型号
  - 光圈、快门速度、ISO、焦距
  - 文件创建日期

- **自动匹配品牌Logo**
  - 根据相机品牌自动选择对应Logo
  - 支持：Nikon、Canon、Sony、Fuji、Hasselblad、Olympus、OM System、Pentax、Panasonic
  - 可自定义Logo图片

- **四种水印样式**
  - `strip` — 白底黑字条形边框（默认，尼康/佳能风格）
  - `transparent` — 半透明水印
  - `border` — 纯色边框
  - `blur` — 模糊边框（取图片边缘模糊，效果自然好看）

- **支持批量处理**
  - 支持整个文件夹批量处理
  - 支持JPG、PNG、TIFF、BMP格式

- **自动旋转校正**
  - 根据EXIF方向信息自动旋转图片

## 快速开始

### 方式1：直接运行exe（推荐）

1. 下载 `photo_watermark.exe`
2. 双击运行，会弹出文件选择对话框
3. 选择图片或文件夹，自动处理并保存

### 方式2：命令行运行

```bash
# 处理单张图片
photo_watermark.exe input.jpg -o output.jpg

# 处理整个文件夹
photo_watermark.exe ./input/ -o ./output/

# 或者用Python运行
python photo_watermark.py input.jpg -o output.jpg
```

## 下载

### 直接下载exe

下载 `photo_watermark.exe`，无需安装Python环境。

### 源码安装

```bash
git clone https://github.com/go-farther-and-farther/photo_watermark.git
cd photo_watermark
pip install -r requirements.txt
```

## 使用方法

### 基本用法

```bash
# 处理单张图片（输出到同目录，自动命名）
photo_watermark.exe input.jpg

# 处理单张图片（指定输出路径）
photo_watermark.exe input.jpg -o output.jpg

# 处理整个文件夹（输出到同级watermark_output文件夹）
photo_watermark.exe ./photos/

# 处理整个文件夹（指定输出目录）
photo_watermark.exe ./photos/ -o ./output/
```

### 选择水印样式

```bash
# 白底黑字条形边框（默认）
photo_watermark.exe input.jpg --style strip

# 半透明水印
photo_watermark.exe input.jpg --style transparent --position bottom-right

# 纯色边框
photo_watermark.exe input.jpg --style border --border-color black

# 模糊边框（推荐，效果自然）
photo_watermark.exe input.jpg --style blur
```

### 自定义文字

```bash
# 添加摄影师名字
photo_watermark.exe input.jpg --text "©摄影师"

# 添加自定义文字 + 指定样式
photo_watermark.exe input.jpg --style border --text "©2026 My Photo"
```

### 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `input` | 输入图片或文件夹路径 | `config.py` 配置（留空则弹出选择） |
| `-o, --output` | 输出路径 | `config.py` 配置（留空则自动命名） |
| `-s, --style` | 边框样式：`strip` / `transparent` / `border` / `blur` | `config.py` 配置 |
| `-t, --text` | 自定义水印文字 | 无 |
| `-p, --position` | 半透明水印位置：`top-left` / `top-right` / `bottom-left` / `bottom-right` | `config.py` 配置 |
| `--border-color` | 边框颜色（`black`/`white`/`gray` 或 RGB 如 `255,255,255`） | `config.py` 配置 |
| `--text-color` | 文字颜色 | `config.py` 配置 |
| `--opacity` | 半透明水印的透明度（0-255） | `config.py` 配置 |
| `--quality` | JPEG输出质量（1-100） | `config.py` 配置 |
| `--logo` | 自定义Logo图片路径 | 自动匹配品牌 |

## 支持的相机品牌

程序会根据照片EXIF中的相机品牌自动匹配对应的Logo：

| 品牌 | Logo文件 |
|------|----------|
| Nikon | `nikon_logo.png` |
| Canon | `canon_logo.png` |
| Sony | `sony_logo.png` |
| Fujifilm | `fuji_logo.png` |
| Hasselblad | `hasselblad_logo.jpeg` |
| Olympus / OM System | `olympus_logo.jpeg` |
| Pentax | `pentax_logo.jpeg` |
| Panasonic | `panasonic_logo.jpeg` |

未匹配到的品牌默认使用 Nikon Logo。

## 自定义配置

编辑 `config.py` 文件来自定义水印样式。所有参数都可以在配置文件中修改，无需命令行参数。

### 通用设置

```python
DEFAULT_STYLE = 'strip'         # 默认水印样式：strip / transparent / border / blur
DEFAULT_INPUT = ''              # 默认输入路径（留空则每次弹出选择对话框）
DEFAULT_OUTPUT = ''             # 默认输出路径（留空则在输入路径同级创建output文件夹）
JPEG_QUALITY = 95               # JPEG输出质量 (1-100)
FONT_SIZE_RATIO = 3             # 字体大小 = 边框高度 / 此值
```

**路径配置说明：**

| 配置项 | 留空 `''` | 设置路径 |
|--------|-----------|----------|
| `DEFAULT_INPUT` | 每次运行弹出文件选择对话框 | 直接使用该路径，不弹窗 |
| `DEFAULT_OUTPUT` | 单张图片：`{原名}_{样式}_watermark.{ext}`；文件夹：创建 `watermark_output` | 输出到指定目录 |

### strip 样式配置（白底黑字条形边框）

```python
BORDER_HEIGHT_RATIO = 0.08      # 边框高度（相对于图片高度）

# 左侧布局（相机型号 + 镜头型号）
LEFT_MARGIN_RATIO = 0.025       # 左侧边距
LINE_SPACING = 6                # 两行文字间距

# 右侧布局（Logo + 拍摄参数 + 日期）
RIGHT_MARGIN_RATIO = 0.025      # 右侧边距
LOGO_PARAMS_SPACING_RATIO = 0.03  # Logo和参数间距
LOGO_HEIGHT_RATIO = 0.7        # Logo高度比例
VERTICAL_OFFSET_RATIO = 0.15    # 文字距顶部偏移

# 文字颜色 (R, G, B)
COLOR_CAMERA = (30, 30, 30)     # 相机型号颜色
COLOR_LENS = (120, 120, 120)    # 镜头型号颜色
COLOR_PARAMS = (30, 30, 30)     # 拍摄参数颜色
COLOR_DATE = (100, 100, 100)    # 日期颜色
COLOR_BORDER = (255, 255, 255)  # 边框背景颜色
```

### transparent 样式配置（半透明水印）

```python
TRANSPARENT_POSITION = 'bottom-right'  # 位置：top-left / top-right / bottom-left / bottom-right
TRANSPARENT_OPACITY = 128              # 透明度 (0-255)
TRANSPARENT_FONT_RATIO = 0.03          # 字体大小比例（相对于图片宽度）
TRANSPARENT_TEXT_COLOR = (255, 255, 255)  # 文字颜色
TRANSPARENT_MARGIN_RATIO = 0.02        # 边距比例
```

### border 样式配置（纯色边框）

```python
BORDER_FRAME_COLOR = (0, 0, 0)         # 边框颜色
BORDER_TEXT_COLOR = (255, 255, 255)     # 文字颜色
BORDER_SIDE_RATIO = 0.04               # 左右两侧边框宽度比例
BORDER_BOTTOM_RATIO = 0.08             # 底部边框宽度比例
```

### blur 样式配置（模糊边框）

```python
BLUR_BORDER_RATIO = 0.06               # 边框宽度比例（相对于图片宽度）
BLUR_INTENSITY = 15                    # 模糊强度（像素），推荐 10-30
BLUR_TEXT_COLOR = (255, 255, 255)       # 文字颜色
BLUR_TEXT_SHADOW = True                 # 是否添加文字阴影
BLUR_CORNER_RADIUS = 0.05              # 圆角半径（相对于图片宽度），0 无圆角
```

**注意：exe用户需要将 `config.py` 和 `logos/` 文件夹放在exe同目录下。**

## 效果预览

### strip 样式（白底黑字条形边框）

```
┌──────────────────────────────────────┐
│           原始照片区域                │
├──────────────────────────────────────┤
│ NIKON Z 7      [Logo] 120mm F5.6 1/125 ISO1100 │
│ NIKKOR Z 24-120mm f/4 S         2026-07-25     │
└──────────────────────────────────────┘
```

### transparent 样式（半透明水印）

```
┌──────────────────────────────────────┐
│                              ┌─────────────┐
│                              │ 水印文字    │
│           原始照片区域        └─────────────┘
│                                              
└──────────────────────────────────────┘
```

### border 样式（纯色边框）

```
┌──────────────────────────────────────┐
│ ┌──────────────────────────────────┐ │
│ │                                  │ │
│ │         原始照片区域              │ │
│ │                                  │ │
│ └──────────────────────────────────┘ │
│        拍摄参数 | 自定义文字         │
└──────────────────────────────────────┘
```

### blur 样式（模糊边框）

```
┌──────────────────────────────────────┐
│  ╭────────────────────────────╮      │
│  │                            │      │
│  │       原图（圆角）          │ 模糊 │
│  │                            │ 边框 │
│  ╰────────────────────────────╯      │
│        120mm F5.6 1/125 ISO1100      │
│            2026-07-26                 │
└──────────────────────────────────────┘
```

## 项目结构

```
photo_watermark/
├── photo_watermark.exe    # 可执行文件（Windows）
├── photo_watermark.py     # 主程序（源码）
├── config.py              # 配置文件
├── logos/                  # Logo文件夹
│   ├── nikon_logo.png
│   ├── canon_logo.png
│   ├── sony_logo.png
│   ├── fuji_logo.png
│   ├── hasselblad_logo.jpeg
│   ├── olympus_logo.jpeg
│   ├── pentax_logo.jpeg
│   └── panasonic_logo.jpeg
├── pkg/                   # 打包配置（开发者用）
│   ├── setup.py
│   └── pyproject.toml
├── requirements.txt       # 依赖
├── README.md              # 说明文档
├── TECHNICAL.md           # 技术文档（开发者参考）
├── LICENSE                # 许可证
└── .gitignore             # Git忽略文件
```

## 依赖

- Python 3.6+（源码运行需要）
- Pillow >= 10.0.0
- exifread >= 3.0.0

## 许可证

MIT License

## 开发者文档

如需参与开发或了解项目内部实现，请参考 [TECHNICAL.md](TECHNICAL.md)，包含：

- 架构概述和模块说明
- 核心函数详解
- 配置系统工作原理
- 数据流图
- 打包说明
- 扩展指南（添加新品牌、新样式等）

## 贡献

欢迎提交Issue和Pull Request！

## 致谢

- [Pillow](https://python-pillow.org/) - Python图像处理库
- [exifread](https://github.com/ianare/exif-py) - EXIF信息读取库
