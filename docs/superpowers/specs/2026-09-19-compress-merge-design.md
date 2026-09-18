# 设计规格：压缩/转换能力并入 photo_watermark

日期：2026-09-19
状态：已获用户批准（方案 A：无水印样式 + 通用输出选项）

## 背景与目标

`image_compress`（D:\Software files\Real-ESRGAN\image_compress）是命令行批量压缩工具，
交互层薄弱（手改 JSON 配置）。photo_watermark 已有完整的 GUI 基建（拖放选图、实时预览、
设置窗口、ini 配置、exe 打包）。两者流水线同构：选文件 → PIL 逐张处理 → JPEG 输出。

目标：把 image_compress 的压缩核心能力以「无水印(仅压缩)」样式的形式并入
photo_watermark，使一个程序同时覆盖加水印和纯压缩两个场景。image_compress 仓库保留，
CLI 继续可用，不改动。

典型场景：12K 游戏截图（11520×6480 RGBA PNG，约 100MB）→ 全分辨率或缩放后转 JPG，
体积降至 10~15MB。

## 已确认的需求决策

| 决策点 | 结论 |
|---|---|
| 集成形态 | 样式列表加「无水印(仅压缩)」，勾选即纯压缩；缩放/目标体积为通用输出设置 |
| 缩放 | 「最大边长」可选档位：保持原尺寸（默认）/ 8K 7680 / 4K 3840 / 2K 2560 / 自定义像素；只缩不放 |
| 输出位置 | 无水印输出到源图旁子文件夹（默认 `JPEG`，可改），文件名保持原名 |
| 目标体积模式 | 第一版包含：设目标 MB，自动二分调质量 [30, 95]，不可达按 q=30 输出并提示 |
| NEF 转换 | 不做（留在 image_compress CLI） |
| 多进程并行 | 不做（串行处理，12K 单张约 1 秒可接受） |
| 源图安全 | 源图永不被修改或覆盖；输出比源文件大时跳过不写 |

## 架构

### 新模块 compressor.py（photo_watermark 目录，约 150 行）

纯函数压缩核心，只依赖 Pillow，不依赖 Tkinter，可独立单测：

```python
resize_image(img, max_edge)        # LANCZOS 等比缩放，只缩不放；max_edge=0 原尺寸
flatten_to_rgb(img)                # RGBA/LA/P 透明合成白底 → RGB；其余模式 convert('RGB')
encode_jpeg_bytes(img, quality, exif=b'', icc=None) -> bytes
encode_to_target_size(img, target_mb, exif=b'', icc=None) -> tuple[bytes, int, bool]
                                   # 返回 (数据, 实际质量, 是否达标)；二分 [30,95]
write_atomic(data, path)           # 临时文件 + os.replace 原子替换
sanitize_subfolder(name) -> str    # 去除 \/:*?"<>| 与首尾空白；空/非法回退 'JPEG'
save_metadata_sidecar(png_path)    # PNG 字符串元数据(ComfyUI 工作流)写同名 .json
```

以上均移植自 image_compress.py（L86-106 的 encode_jpeg/write_atomic、L182-206 的
RGBA 白底合成与元数据 sidecar），行为保持一致。

### photo_watermark.py 接线点（4 处，行号为当前版本）

1. **STYLE_OPTIONS**（L2785）：追加 `('none', '无水印(仅压缩)', '不加水印，仅压缩/转换输出 JPG')`。
   勾选框、设置窗口默认样式下拉、预览网格均由该列表驱动，自动生效。
2. **apply_single_style**（L1605-1664 的 if/elif 链）：`elif style == 'none': return image`。
3. **main() 的 valid_styles**（L3933）：集合加 `'none'`。
4. **process_single_image**（L1680）：管线插入缩放与无水印输出逻辑（见下节）。

打包：compressor.py 被正常 import 即可，spec 无需修改。

## 处理管线（数据流）

现有管线不变，插入三处：

> 读图 → **【新】捕获 raw_exif/icc/png 元数据**（Image.open 后立刻取
> `image.info` 的 `exif`/`icc_profile`）→ EXIF 自动旋转 →
> **【新】resize_image（通用，对所有样式生效；先缩放再画水印，保证边框比例正确）** →
> 套样式（`none` = 原图直通）→ **【改】保存分支** → 结束

保存分支（L1796-1803 处）：

- **样式 ≠ none**：维持现状（JPEG 用 quality=JPEG_QUALITY + subsampling；PNG 直存）。
  目标体积模式不作用于水印输出。
- **样式 = none**：
  1. 输出路径固定为 `<源图所在目录>/<压缩子文件夹>/<源文件主名>.jpg`（在
     process_single_image 内覆盖，单文件/多文件/文件夹批量/CLI 四种入口统一生效，
     调用方 main()/process_multiple_files/batch_process 无需改动）；
  2. 目标体积 > 0 → `encode_to_target_size`，否则 `encode_jpeg_bytes`
     （quality=JPEG_QUALITY，optimize=True，保留 exif/icc）；
  3. PNG 源含字符串元数据 → 写同名 `.json` sidecar（已存在则跳过）；
  4. 输出字节 ≥ 源文件大小 → 跳过不写，日志说明（重编码已充分压缩的小 JPG 时可能触发）；
  5. `write_atomic` 落盘；**已存在的旧输出直接覆盖**（派生文件，重跑幂等；
     不走 unique_output_path / OVERWRITE_EXISTING）。

### 顺带的针对性修复

现有 L1710-1713 对 RGBA 是裸 `convert('RGB')`（丢 alpha，透明区域可能异常），
替换为 `flatten_to_rgb`——对所有水印样式是行为改进，白底合成与 JPEG 输出语义一致。

## 输出路径规则汇总

| 场景 | 输出 |
|---|---|
| none（任意入口） | `<源目录>/<压缩子文件夹>/<原名>.jpg`，覆盖旧输出 |
| 水印样式 | 完全不变（文件夹模式 watermark_output；单文件模式 OUTPUT_FILENAME_FORMAT） |
| none + 水印同时勾选 | 各自独立输出，互不影响 |

## UI 变化

主选择窗口：样式列表多一项「无水印(仅压缩)」；预览 = 缩放后的原图（render_preview
走 apply_single_style 直通，无需改动）。

设置窗口「总设置 → 输出设置」组（g3，L2607）新增三行，控件与现有模式一致：

| 设置 | 控件 | 默认 | 说明 |
|---|---|---|---|
| 最大边长 | ttk.Combobox（可编辑）：保持原尺寸 / 8K (7680) / 4K (3840) / 2K (2560)，也可直接输入像素数 | 保持原尺寸 | 只缩不放；对所有样式生效 |
| 目标体积 (MB) | Entry，0=关闭 | 0 | 仅对无水印输出生效 |
| 压缩输出子文件夹 | Entry | JPEG | 无水印输出的子文件夹名 |

接入现有机制：`on_save` 的 save_items（L2493）、`on_restore_defaults`（gd 调用，
L2461）、`refresh_globals_from_ini`（L1999）各加对应条目。

## 配置存储

三层优先级不变（用户设置.ini > 水印设置.ini > config.py）：

- `用户设置.ini`：新 `[压缩设置]` 节（update_ini_value 自动建节）：
  `最大边长=0`、`目标体积MB=0`、`压缩输出子文件夹=JPEG`
- `水印设置.ini`（默认模板）：同步加 `[压缩设置]` 节同默认值
- `config.py`：`COMPRESS_MAX_EDGE = 0`、`COMPRESS_TARGET_MB = 0`、`COMPRESS_SUBFOLDER = 'JPEG'`
- 全局变量在 L210-296 区块的初始化处补充

## 错误处理

- 无 EXIF（游戏截图）：read_exif 已安全返回空字典 + 文件时间兜底，无影响
- 目标体积不可达（q=30 仍超）：按 q=30 输出，日志 `[提示] 无法压到目标体积，已按质量30输出`
- 压缩子文件夹名非法：sanitize_subfolder 回退 `JPEG`
- 输出变大：跳过 + 日志说明，源图不动
- 超大图：保留 PIL 默认 DecompressionBomb 防护（约 1.78 亿像素上限；12K=7460 万像素
  远低于此），现有 L1809 的优雅失败处理已覆盖。**不**全局设 MAX_IMAGE_PIXELS=None
  （避免关闭整个应用的防护，与此前口头设计有一处修正）

## 测试计划

devtests/ 新增 compressor 单测（pytest）：

1. resize_image：0=原尺寸；小于原边长缩放；大于原边长不放大；比例保持
2. flatten_to_rgb：RGBA 透明区域为白色；RGB/L/16位模式直通或正确转换
3. encode_to_target_size：大图设宽松目标可达；设极小目标降级 q=30 且返回未达标标志
4. 变大跳过：已充分压缩的小 JPG 重编码触发跳过
5. write_atomic：目标已存在时成功替换；写入失败不留 .tmp
6. sanitize_subfolder：非法字符清除；空值回退 JPEG
7. sidecar：含字符串元数据的 PNG 生成 .json；已存在不覆盖

手动验收：

- 两张 12K 截图（20260919_005609_573/20260919_005844_204）过 GUI：勾「无水印」→
  JPEG/ 子文件夹出同名 .jpg，约 100MB → 12~15MB（与本次手工验证结果一致）
- 最大边长 4K：输出 3840×2160
- 目标体积 10MB：输出 ≤10MB
- 回归：勾选各水印样式输出与改动前一致（含 watermark_output 命名、EXIF 旋转）

## 范围外（明确不做）

- NEF/RAW 转 JPG（留在 image_compress CLI）
- 多进程并行批处理
- 目标体积作用于水印样式
- image_compress 仓库的任何改动
