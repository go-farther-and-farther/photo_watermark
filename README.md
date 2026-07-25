# Photo Watermark - 相机照片水印边框生成器

给相机照片添加水印边框，显示拍摄参数（EXIF信息），支持批量处理。

![示例效果](test_output22.jpg)

## 功能特点

- **自动读取EXIF拍摄参数**
  - 相机型号、镜头型号
  - 光圈、快门速度、ISO、焦距
  - 文件创建日期

- **可自定义布局参数**
  - 边框大小、字体大小
  - Logo位置和大小
  - 文字颜色和间距

- **支持批量处理**
  - 支持整个文件夹批量处理
  - 支持JPG、PNG、TIFF、BMP格式

- **内置Nikon Logo**
  - 默认使用Nikon风格Logo
  - 可自定义Logo图片

## 安装

```bash
pip install -r requirements.txt
```

或手动安装：

```bash
pip install Pillow exifread
```

## 使用方法

### 基本用法

```bash
# 处理单张图片（使用默认布局）
python photo_watermark.py input.jpg -o output.jpg

# 处理整个文件夹
python photo_watermark.py ./input/ -o ./output/
```

### 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `input` | 输入图片或文件夹路径 | `./input` |
| `-o, --output` | 输出路径 | `./output` |
| `-s, --style` | 边框样式 | `white` |
| `-t, --text` | 自定义水印文字 | 空 |
| `--quality` | JPEG输出质量 (1-100) | `95` |
| `--logo` | Logo图片路径 | `nikon_logo.png` |

## 自定义布局

编辑 `photo_watermark.py` 顶部的布局参数：

```python
# ========== 布局参数（手动调整区域） ==========
# 边框设置
BORDER_HEIGHT_RATIO = 0.08      # 边框高度（相对于图片高度）

# 左侧布局
LEFT_MARGIN_RATIO = 0.025       # 左侧边距
LINE_SPACING = 6                # 两行文字间距

# 右侧布局
RIGHT_MARGIN_RATIO = 0.025      # 右侧边距
LOGO_PARAMS_SPACING_RATIO = 0.03  # Logo和参数间距
LOGO_HEIGHT_RATIO = 0.7        # Logo高度比例

# 字体设置
FONT_SIZE_RATIO = 3             # 字体大小比例

# 文字颜色 (R, G, B)
COLOR_CAMERA = (30, 30, 30)     # 相机型号颜色
COLOR_LENS = (120, 120, 120)    # 镜头型号颜色
COLOR_PARAMS = (30, 30, 30)     # 拍摄参数颜色
COLOR_DATE = (100, 100, 100)    # 日期颜色
```

## 效果预览

处理后的图片底部会显示：

```
[左侧]                    [Logo] [右侧参数]
NIKON Z 7                  120mm F5.6 1/125 ISO1100
NIKKOR Z 24-120mm f/4 S            2026-07-25
```

## 项目结构

```
photo_watermark/
├── photo_watermark.py    # 主程序
├── nikon_logo.png        # Nikon Logo
├── requirements.txt      # 依赖
├── README.md            # 说明文档
├── LICENSE              # 许可证
└── .gitignore           # Git忽略文件
```

## 依赖

- Python 3.6+
- Pillow
- exifread

## 许可证

MIT License

## 贡献

欢迎提交Issue和Pull Request！

## 致谢

- [Pillow](https://python-pillow.org/) - Python图像处理库
- [exifread](https://github.com/ianare/exif-py) - EXIF信息读取库
