# ========== 水印配置文件 ==========
# 修改此文件来自定义水印样式，无需修改代码

# ==================== 通用设置 ====================

# 默认水印样式
# strip       - 白底黑字条形边框，照片下方显示相机型号、镜头、拍摄参数、日期
# transparent - 半透明文字水印，直接叠加在照片上，可选位置（左上/右上/左下/右下）
# border      - 纯色边框包裹照片，底部显示拍摄参数，黑色边框经典，透明边框也不错
# blur        - 模糊边框，取图片边缘做高斯模糊作为边框，效果自然好看
DEFAULT_STYLE = 'strip'

# 默认Logo（留空则自动根据品牌选择）
DEFAULT_LOGO = ''

# 默认输入路径（留空则每次弹出选择对话框）
DEFAULT_INPUT = './input'

# 默认输出路径（留空则在输入路径同级创建 output 文件夹）
DEFAULT_OUTPUT = './output'

# JPEG输出质量 (1-100)
JPEG_QUALITY = 95

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
