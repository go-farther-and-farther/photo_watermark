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

- **自定义边框背景图**
  - 可自由选择一张图片作为水印边框的背景（strip/纯色边框样式生效）
  - 背景图透明度可调（半透明效果，照片边缘透出来更自然）

- **图形化设置窗口**
  - 选择窗口里点「设置...」，所有常用配置（签名、样式、背景图、透明度、颜色、输出等）都能在窗口里改，保存后立即生效
  - 设置保存在 `水印设置.ini` 中，无需手动编辑文件

- **支持批量处理**
  - 支持整个文件夹批量处理
  - 支持JPG、PNG、TIFF、BMP格式

- **自动旋转校正**
  - 根据EXIF方向信息自动旋转图片

## 快速开始

### 方式1：直接运行exe（推荐）

1. 下载 `photo_watermark.exe`
2. 双击运行，弹出窗口：
   - 勾选水印样式（可多选，默认已选配置中的样式）
   - 点「选择照片」（可多选）或「选择文件夹（批量处理）」
3. 自动处理并保存：
   - 单张/多张照片 → 输出到照片所在目录（自动命名）
   - 文件夹 → 输出到其下的 `watermark_output` 文件夹
4. 处理完**不会退出**，自动回到选择窗口，可继续选择下一批照片；点「取消」退出

**快捷方式：拖拽** —— 打开程序后，直接把照片/文件夹**拖到选择窗口上**松手即可（会提示已选数量），调整样式后点「开始处理」；也可以多选后直接拖到 `photo_watermark.exe` 图标上（自动弹样式窗口）。

默认隐藏黑窗口（可在 `水印设置.ini` 中改「显示控制台窗口」），处理完成或出错时用弹窗提示。
如果输出文件已存在，会跳过并提示（可删除旧文件，或在设置中开启覆盖）。

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

**方式A：图形界面选择（最常用，无需命令行）**

双击exe（或运行 `python photo_watermark.py`），弹出窗口后：
- 勾选水印样式（可多选，默认勾选配置中的样式）
- 点「选择照片」→ 在资源管理器中可**多选**照片（Ctrl/Shift），处理结果保存到照片所在目录
- 点「选择文件夹」→ 批量处理文件夹内所有照片，结果保存到其下的 `watermark_output` 文件夹
- 点「设置...」→ 打开设置窗口，可改签名、样式、边框背景图（可半透明）、颜色、输出等，保存后立即生效
- 处理完自动回到选择窗口，可继续处理下一批；点「取消」退出

**方式B：命令行**

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
| `input` | 输入图片或文件夹路径 | 留空则弹出窗口选择（`水印设置.ini` 可设置默认路径） |
| `-o, --output` | 输出路径 | `config.py` 配置（留空则自动命名） |
| `-s, --style` | 边框样式：`strip` / `transparent` / `border` / `blur` | `config.py` 配置 |
| `-t, --text` | 自定义水印文字 | 无 |
| `-p, --position` | 半透明水印位置：`top-left` / `top-right` / `bottom-left` / `bottom-right` | `config.py` 配置 |
| `--border-color` | 边框颜色（`black`/`white`/`gray` 或 RGB 如 `255,255,255`） | `config.py` 配置 |
| `--text-color` | 文字颜色 | `config.py` 配置 |
| `--opacity` | 半透明水印的透明度（0-255） | `config.py` 配置 |
| `--quality` | JPEG输出质量（1-100） | `config.py` 配置 |
| `--logo` | 自定义Logo图片路径 | 自动匹配品牌 |

## 支持的品牌

程序会根据照片EXIF中的品牌自动匹配对应的Logo（相机 + 手机/无人机）：

| 品牌 | Logo文件 | 品牌 | Logo文件 |
|------|----------|------|----------|
| Nikon | `nikon_logo.png` | 小米 | `xiaomi_logo.png` |
| Canon | `canon_logo.png` | 华为 | `huawei_logo.png` |
| Sony | `sony_logo.png` | 荣耀 | `honor_logo.png` |
| Fujifilm | `fuji_logo.png` | 苹果 | `apple_logo.png` |
| Hasselblad | `hasselblad_logo.png` | 大疆 | `dji_logo.png` |
| Olympus / OM System | `olympus_logo.png` | 三星 | `samsung_logo.png` |
| Pentax | `pentax_logo.png` | 谷歌 | `google_logo.png` |
| Panasonic | `panasonic_logo.png` | vivo / OPPO | `vivo_logo.png` / `oppo_logo.png` |
| Ricoh | `ricoh_logo.png` | 徕卡 | `leica_logo.png` |

- 未匹配到品牌或 Logo 文件缺失时不使用 Logo（不影响水印文字）
- 手机/无人机品牌的 Logo 文件可自行放入 `logos/` 文件夹（如 `xiaomi_logo.png`）
- **手动选择Logo**：每次处理时可在**选择窗口**（或拖拽弹出的样式窗口）里的「品牌Logo」下拉直接指定品牌（如选「哈苏」就强制用哈苏Logo），比EXIF自动识别更灵活；也可在设置窗口 → 品牌 里设全局默认

## 自定义配置

**三层配置（默认 + 个人 + 代码兜底）：**

| 文件 | 作用 | 更新程序时 |
|------|------|-----------|
| `水印设置.ini` | 默认配置模板（带注释说明），随程序发布 | 随新版一起更新 |
| `用户设置.ini` | **你的个人设置**，由设置窗口自动生成（已加入 .gitignore） | 保留，不会被覆盖 |
| `config.py` | 代码默认值（开发者/命令行兜底） | 随新版一起更新 |

程序启动时按 **默认ini → 个人ini（覆盖）→ config.py 兜底** 加载。**个人设置存独立文件的好处**：以后更新程序只需替换 exe 和 `水印设置.ini`，你的签名、样式等个人设置原样保留；新版新增的配置项自动用新默认值。

exe 用户只需要 `config.py`、`水印设置.ini`、`logos/` 与 exe 放一起（`用户设置.ini` 会在第一次保存设置时自动生成在同目录）。

**推荐方式：在设置窗口里改配置**（选择窗口 → 设置...），保存后立即生效，无需手动编辑任何文件。

### 通用设置

```python
DEFAULT_STYLE = 'strip'         # 默认水印样式：strip / transparent / border / blur
DEFAULT_INPUT = ''              # 默认输入路径（留空则弹出窗口选择）
DEFAULT_OUTPUT = ''             # 默认输出路径（留空则自动处理，见下表）
JPEG_QUALITY = 95               # JPEG输出质量 (1-100)
JPEG_SUBSAMPLING = 0            # JPEG色度采样：0=4:4:4最清晰 1=4:2:2 2=4:2:0文件小
FONT_SIZE_RATIO = 3             # 字体大小 = 边框高度 / 此值
SHOW_CONSOLE_WINDOW = False     # 是否显示控制台窗口（exe模式生效，False=隐藏黑窗口）
BORDER_BACKGROUND_IMAGE = ''    # 边框背景图路径（留空=纯色背景）
BORDER_BACKGROUND_OPACITY = 128 # 边框背景图透明度 (0-255)，128=半透明
```

**路径配置说明：**

| 配置项 | 留空 `''`（推荐） | 设置路径 |
|--------|-----------|----------|
| `DEFAULT_INPUT` | 弹出窗口选择照片（可多选）或文件夹 | 直接使用该路径，不弹窗 |
| `DEFAULT_OUTPUT` | 单张/多张照片：输出到照片所在目录 `{原名}_{样式}_watermark.{ext}`；文件夹：创建 `watermark_output` | 输出到指定目录 |

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

**注意：exe用户需要将 `config.py`、`水印设置.ini` 和 `logos/` 文件夹放在exe同目录下。**

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

**依赖库：**

- [Pillow](https://python-pillow.org/) - Python图像处理库
- [exifread](https://github.com/ianare/exif-py) - EXIF信息读取库
