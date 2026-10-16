# 手持多帧超分辨率合成工具

将手持连拍 RAW/JPG 合成为高分辨率图像，保留真实细节，适合 Lightroom 后期调色。

## 快速开始

```bash
# 安装依赖
pip install rawpy opencv-python numpy tifffile exifread piexif

# 运行
python sr_process.py --input ./你的RAW文件夹/ --output result.tif
```

## 命令行参数

```bash
python sr_process.py \
  --input ./raw_burst/ \       # 输入文件夹（支持 RAW/JPG/PNG/TIFF）
  --output result.tif \         # 输出路径（自动同时输出 TIFF + JPG）
  --scale 2 \                   # 超分倍率（默认 2）
  --max-frames 16 \             # 最大使用帧数（默认 16）
  --max-shift 50 \              # 最大允许位移/像素（默认 50）
  --sharpen 0.5 \               # 锐化强度 0-1（默认 0.3）
  --denoise                     # 启用降噪
```

## 输出

| 格式 | 说明 |
|------|------|
| 16-bit TIFF | 无压缩，可直接进 Lightroom 调色；EXIF 写入 Make/Model/拍摄时间（快门/ISO/镜头等在 Exif 子 IFD，tifffile 不支持写，完整 EXIF 见 JPG） |
| JPG | 预览用，quality 90，带完整 EXIF |

## 算法概述

```
输入 → 中心裁切对齐(半尺寸) → 位移全分辨率精修 → 帧筛选 → SR网格直接融合 → 后处理 → 输出
```

### 1. 帧间对齐（中心裁切 + 相位相关）

- 读取灰度图 + gamma 校正（线性 RAW 太暗）
- 中心裁切 60%，半尺寸相位相关估计亚像素位移
- 全分辨率精修残差（`_refine_shift`，最终误差 ~0.04px，见算法升级记录）
- 估计帧间光度增益/偏置并校正（`_estimate_gain_bias`）

### 2. 帧筛选

- 先找最佳参考帧（位移最接近中位数的帧）
- 过滤位移过大的帧（对齐不准会重影）
- 贪心选择亚像素分布最均匀的 M 帧

### 3. SR 网格直接融合（v2 核心）

- 每帧按含小数部分的位移**直接 remap 到超分网格**——各帧采样落在
  网格的不同亚相位上，融合才能增加细节（旧版先对齐回参考帧网格，
  亚像素相位在插值中被抹掉，只剩降噪，可用 `--legacy` 调用）
- 分块鲁棒融合：逐像素中位数为锚压低离群帧（运动/鬼影）
- 详见 `算法升级记录.md`

### 4. 后处理

- sRGB gamma 校正
- 自动亮度拉伸（去 0.5% 极值）
- 多尺度锐化（细节+边缘）
- 写入 EXIF 元数据（TIFF 经 tifffile extratags，JPG 经 piexif）

## 当前限制

1. **全局平移假设** — 只能处理平移，有景深时前景背景位移不同
2. **插值融合** — remap+平均只能部分消除混叠，还不能完全反演欠采样算子（下一步：正则化反演/核回归）
3. **简单去鬼影** — 中位数锚定的鲁棒权重，半透明/大面积运动处理不好
4. **RGB 域处理** — 先解拜尔再超分，浪费了 Bayer 模式信息

## 已规划改进

- [ ] 分块相位相关（Block-based）— 解决景深视差
- [ ] 核回归融合（替代 Bicubic）— 显著提升锐度
- [ ] RAW 域超分（Bayer SR）— 色彩和细节最好
- [ ] 频域去鬼影 — 运动区域更自然

## 拍摄建议

| 要求 | 说明 |
|------|------|
| 连拍数量 | 16-25 张 |
| 连拍速度 | 越快越好（30fps+ 最佳） |
| 手持方式 | 靠栏杆/墙壁稳定，但不要完全固定 |
| 构图 | 尽量保持相同构图，靠手的自然抖动提供亚像素位移 |

## 项目结构

已并入 photo_watermark 仓库（原独立目录 `super_res/`），模块在仓库根目录、文档在本目录：

```
photo_watermark/
├── sr_process.py          # 主程序入口（v2: SR网格鲁棒融合; --legacy 为旧算法）
├── sr_gui.py              # 连拍分组版 GUI（水印工具标题栏「🔬 超分」按钮打开同一窗口）
├── align.py               # 帧间对齐（相位相关）
├── burst_tools.py         # 连拍自动分组 + 选帧（按拍摄时间/清晰度）
├── super_res.py           # 锐化/降噪后处理（reconstruct_sr 已废弃未用）
└── docs/super_res/        # 本目录：README / 算法设计文档 / 算法升级记录 / 技术实现分享 / 修复记录
```

> 搬运时未带走 `deghost.py`、`raw_reader.py`、`config.py`（三个均为死代码，
> 见 docs/super_res/修复记录.md 第五节）；v2 的去鬼影已内置于 sr_process 的鲁棒融合。

## 测试数据

```
D:\photo\JPG\test\
├── test4/   # 18张 NEF 连拍（6064x4040，24MP）
├── test7/   # 24张 NEF 连拍
└── test8/   # test4 裁切中心 1/2（3032x2020，JPG）
```

## 依赖

- rawpy — RAW 解拜尔
- opencv-python — 图像处理
- numpy — 数组运算
- tifffile — TIFF 读写
- exifread — 读取 EXIF
- piexif — 写入 EXIF
