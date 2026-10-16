# Linear DNG 母版输出设计（超分管线 v3）

**日期**：2026-10-16
**状态**：已确认（用户选定路线 A）
**动机**：现行 sRGB TIF 输出把白平衡、gamma、自动亮度烧进像素，Lightroom 无法调 Kelvin
色温（只能当普通图调）。参照 Adobe Super Resolution：输出 Linear DNG，保留 raw 可编辑性。

## 核心决策

1. **线性相机色彩空间融合**：RAW 帧改用 `rawpy.postprocess(use_camera_wb=False,
   output_color=ColorSpace.raw, gamma=(1,1))` 解码——线性、相机原生 RGB、不烧白平衡。
   对齐/运动检测/光度校正照旧（灰度相关对色彩空间不敏感）。
2. **后处理全部跳过**：不锐化、不降噪、不 sRGB gamma、不自动亮度拉伸——全部留给 LR。
   （锐化/降噪本来就该在 LR 里按输出尺寸做。）
3. **元数据自包含**（每张 RAW 自带，零硬编码机身表）：
   - `ColorMatrix1` = `rawpy.rgb_xyz_matrix[:3,:3]`（XYZ→相机，即 dcraw/Adobe cam_xyz）
   - `AsShotNeutral` = 1 / `rawpy.camera_whitebalance`（RGB 三通道，拍摄白平衡）
   - `BlackLevel=0 / WhiteLevel=65535`（postprocess 输出已做黑电平扣除与归一化）
4. **DNG 结构**：tifffile 写 TIFF + DNG extratags（DNGVersion 1.4、LinearRaw photometric
   34892、3 samples 16bit、**无压缩**——Lightroom 对 LinearRaw 只稳定支持 JPEG-LL/无压缩，
   Adobe Deflate 实测被拒收）；完整 EXIF 仍经 ExifTool 从参考帧拷入（DNG 即 TIFF
   结构，ExifTool 直接可写）。
5. **输出组合**（RAW 组）：`*_2x.dng`（母版）+ `*_2x.jpg`（预览：WB 后经**实测回归的
   相机→线性sRGB 矩阵**渲染——libraw 内部矩阵约定与理论公式 inv(cam_xyz)@sRGB 不一致，
   理论路线曾导致洋红偏色，改为同帧两种解码最小二乘回归；仅预览做轻度自动拉伸）。
   TIF 在 DNG 模式下不再输出。
6. **回退**：JPG 连拍无相机空间 → 维持原 TIF+JPG；矩阵缺失/异常 → 回退 TIF 并打印原因。

## 验收

- 单测：neutral 换算、sRGB 渲染、DNG 写入后 rawpy 可重开（尺寸一致）、exiftool 识别
  File Type = DNG 且 ColorMatrix1/AsShotNeutral 存在、JPG 输入自动回退 TIF
- 真实验收：100NCZ_6 重合成 → exiftool 确认 DNG+镜头信息 → 用户在 LR 里确认 Kelvin
  色温、相机配置文件、镜头矫正可用
- 预览色与旧版 JPG 大体一致（同为该场景的合理渲染，非严格相等——旧版有自动拉伸）
