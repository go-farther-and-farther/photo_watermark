# ========== 水印配置文件 ==========
# 修改此文件来自定义水印样式，无需修改代码

# ==================== 通用设置 ====================

# 默认水印样式
# strip       - 白底黑字条形边框，照片下方显示相机型号、镜头、拍摄参数、日期
# transparent - 半透明文字水印，直接叠加在照片上，可选位置（左上/右上/左下/右下）
# border      - 纯色边框包裹照片，底部显示拍摄参数，黑色边框经典，透明边框也不错
# blur        - 模糊边框，取图片边缘做高斯模糊作为边框，效果自然好看
DEFAULT_STYLE = 'strip'

# 智能样式选择（根据照片方向自动选择）
# 'auto' - 根据照片方向自动选择样式
# 其他值 - 固定使用指定样式
SMART_STYLE = 'auto'
LANDSCAPE_STYLE = 'strip'      # 横版照片默认样式
PORTRAIT_STYLE = 'blur'        # 竖版照片默认样式
SQUARE_STYLE = 'transparent'   # 方形照片默认样式

# 默认Logo（留空则自动根据品牌选择）
DEFAULT_LOGO = ''

# 手动指定品牌（留空=自动识别EXIF品牌；指定如 'NIKON'/'XIAOMI' 则强制用该品牌匹配Logo，
# 适合没有EXIF信息的手机照片。可在设置窗口里选择）
DEFAULT_BRAND = ''

# 默认自定义文字/签名（留空则不显示，命令行 --text 可覆盖）
# 示例：'©我的签名'  'Photographer Name'
DEFAULT_TEXT = ''

# 默认输入路径（留空则弹出图形界面选择照片/文件夹，推荐；设置路径则不弹窗直接处理）
DEFAULT_INPUT = ''

# 默认输出路径（留空则自动处理：单张/多张照片输出到照片所在目录，文件夹输出到其下的 watermark_output）
DEFAULT_OUTPUT = ''

# JPEG输出质量 (1-100)，98 接近原图质量，文件大小合理
JPEG_QUALITY = 100

# JPEG色度采样（0=4:4:4 最清晰，1=4:2:2，2=4:2:0 文件最小）
# 摄影师建议 0（保留完整色彩信息）；默认 0
JPEG_SUBSAMPLING = 0

# 是否显示控制台窗口（exe模式生效；源码运行时始终显示，便于调试）
# False = 隐藏黑窗口（默认），处理完成/出错时用弹窗提示
SHOW_CONSOLE_WINDOW = False

# 边框背景图（留空=纯色背景；设置图片路径后，strip白条/纯色边框的底色用该图，可半透明）
BORDER_BACKGROUND_IMAGE = ''

# 边框背景图透明度 (0-255)，255=完全不透明，128=半透明（推荐），越小越透
BORDER_BACKGROUND_OPACITY = 128

# 居中Logo水印（center样式）Logo透明度 (0-255)，100=半透明
CENTER_LOGO_OPACITY = 100

# 居中Logo水印（center样式）Logo高度占图片高度的比例（0.05-0.30），0.12=适中
CENTER_LOGO_RATIO = 0.12

# 字体设置
FONT_SIZE_RATIO = 3             # 字体大小 = 边框高度 / 此值

# ==================== white 样式（白底黑字边框） ====================

# 边框设置
BORDER_HEIGHT_RATIO = 0.08      # 边框高度（相对于图片高度，默认8%）

# 左侧布局（相机型号 + 镜头型号）
LEFT_MARGIN_RATIO = 0.025       # 左侧边距（相对于图片宽度）
LINE_SPACING = 6                # 两行文字间距（像素）

# 右侧布局（Logo + 拍摄参数 + 日期）
RIGHT_MARGIN_RATIO = 0.025      # 右侧边距（相对于图片宽度）
LOGO_PARAMS_SPACING_RATIO = 0.03  # Logo和参数之间的间距（相对于图片宽度）
LOGO_HEIGHT_RATIO = 0.7        # Logo高度占边框高度的比例

# 垂直位置
VERTICAL_OFFSET_RATIO = 0.15    # 文字距顶部的偏移（相对于边框高度）

# 文字颜色 (R, G, B)
COLOR_CAMERA = (30, 30, 30)     # 相机型号颜色
COLOR_LENS = (120, 120, 120)    # 镜头型号颜色
COLOR_PARAMS = (30, 30, 30)     # 拍摄参数颜色
COLOR_DATE = (100, 100, 100)    # 日期颜色
COLOR_BORDER = (255, 255, 255)  # 边框背景颜色

# ==================== transparent 样式（半透明水印） ====================

# 位置：top-left / top-right / bottom-left / bottom-right
TRANSPARENT_POSITION = 'bottom-right'

# 透明度 (0-255)，0=完全透明，255=完全不透明
TRANSPARENT_OPACITY = 128

# 字体大小比例（相对于图片宽度）
TRANSPARENT_FONT_RATIO = 0.03

# 文字颜色 (R, G, B)
TRANSPARENT_TEXT_COLOR = (255, 255, 255)  # 白色

# 边距比例（相对于图片宽度）
TRANSPARENT_MARGIN_RATIO = 0.02

# ==================== border 样式（纯色边框） ====================

# 边框颜色 (R, G, B)
# 黑色 (0, 0, 0) 经典，白色 (255, 255, 255) 简约，透明也不错
BORDER_FRAME_COLOR = (0, 0, 0)        # 黑色边框

# 文字颜色 (R, G, B)
BORDER_TEXT_COLOR = (255, 255, 255)    # 白色文字

# 边框宽度比例（相对于图片宽度，左右两侧）
BORDER_SIDE_RATIO = 0.04

# 底部边框宽度比例（相对于图片宽度，比两侧更宽）
BORDER_BOTTOM_RATIO = 0.08

# ==================== blur 样式（模糊边框） ====================

# 边框宽度比例（相对于图片宽度）
BLUR_BORDER_RATIO = 0.06

# 模糊强度（像素），推荐 10-30
BLUR_INTENSITY = 15

# 文字颜色 (R, G, B)
BLUR_TEXT_COLOR = (255, 255, 255)    # 白色文字

# 是否添加文字阴影（在模糊背景上更清晰）
BLUR_TEXT_SHADOW = True

# 圆角半径（相对于图片宽度），0 表示无圆角
BLUR_CORNER_RADIUS = 0.03

# blur 样式内部参数
BLUR_BOTTOM_RATIO_MULTIPLIER = 1.8  # 底部边框宽度倍数
BLUR_BRIGHTNESS_FACTOR = 0.85       # 亮度降低因子（0.8-0.95）
BLUR_DOWNSAMPLE_FACTOR = 4          # 缩小倍数（越大越快，但质量略降）

# ==================== 输出设置 ====================

# 输出文件命名格式（支持变量：{name}, {style}）
OUTPUT_FILENAME_FORMAT = '{name}_{style}_watermark'

# 是否覆盖已存在的文件
OVERWRITE_EXISTING = False

# 处理完成后自动打开输出目录
AUTO_OPEN_OUTPUT = True
