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
| 16-bit TIFF | 无压缩，可直接进 Lightroom 调色 |
| JPG | 预览用，quality 90 |

**EXIF 继承**：装有 ExifTool（水印工具已内置查找）时，参考帧的完整 EXIF——镜头型号、
快门、ISO、焦距等——整体拷入 TIF 和 JPG（尺寸标签改写为输出实际值）。Lightroom 打开后
依据 LensModel **自动挂镜头矫正配置**，拍摄信息完整可检索。连拍各帧参数一致，取参考帧即可。
ExifTool 不可用时回退：JPG 写基础字段（piexif），TIFF 只写 Make/Model/DateTime（tifffile extratags）。

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

## 自动扫描判定（sr_suggest）

给一个装着几百上千张照片的文件夹（RAW+JPG 混放即可），程序自动找出连拍组并判定能否超分：

- **第一层·快筛**（秒级）：线程池只读 EXIF 拍摄时间分组；同快门的 RAW+JPG 双格式只留 RAW；
  不足 3 帧的组不进表格、只计入汇总
- **第二层·深判**（每组几秒，可取消保留部分结果）：抽 8 帧对齐，量位移/内容运动/亚像素多样性
- 四档判定：**✅ 可超分**（默认勾选）/ **🧊 仅降噪**（三脚架近零位移）/ **❌ 不可**（帧数不足、
  抖动过大、内容在动）/ **✔️ 已完成**（输出目录 `.sr_done.json` 清单命中，组指纹=文件名+大小哈希，
  与组号无关；手动勾选=重做，输出名自动 _2/_3 唯一化）
- 命令行等价入口：`from sr_suggest import scan_and_judge`（见 tests/test_sr_suggest.py 用法）

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
