# EXIF 信息查看器（快门数）集成设计

## 背景

用户需要本地查看相机照片（NEF/JPEG）的拍摄参数，重点是**快门数**，不想再上传到网页查询。现有工程 `photo_watermark` 是 tkinter + PyInstaller 单文件 exe 的水印工具，用户要求把 EXIF 查看功能**集成进水印工具**（方案 A：独立窗口），并**打两个包**：完整版（内置 ExifTool）与精简版（无 ExifTool）。

## 方案总览

- **一份代码，两套打包**：EXIF 读取按优先级自动选择 `内置 ExifTool → 系统 ExifTool → exifread`。
  - 完整版 `photo_watermark.exe`（~40MB）：内置 ExifTool.exe，任何机身/格式通吃。
  - 精简版 `photo_watermark_lite.exe`（~23MB）：无 ExifTool，exifread 解析。实测 Z7 II 的 NEF 全部字段（含快门数 571）可读；其他品牌/机身可能读不到快门数。
- **入口**：主窗口标题栏新增「📷 EXIF」按钮（与 ⚙ 设置并列），打开独立 EXIF 窗口；主窗口已选照片可一键发送到 EXIF 窗口。

## 已实测验证（DSC_0567.NEF）

- ExifTool：`ShutterCount = 571`，机身 NIKON Z 7_2，镜头 NIKKOR Z 24-120mm f/4 S。
- exifread（纯 Python，无 ExifTool）：机身/光圈 F4/快门 1/8000/ISO 64/焦距 33mm/镜头/时间全部可读；`MakerNote TotalShutterReleases = 571`、`MakerNote MechanicalShutterCount = 571`。

## 架构与组件

1. **`exif_reader.py`（新模块）** — 纯函数，可单测：
   - `find_exiftool()`：依次找打包内置路径、环境变量、系统 PATH。
   - `read_exif(path)`：返回统一字典 `{model, shutter_count, lens, exposure_time, fnumber, iso, focal_length, datetime, ...}`。
     - 有 ExifTool：`-j` JSON 批量输出，字段取 `Model/ShutterCount/LensID/ExposureTime/FNumber/ISO/FocalLength/CreateDate`。
     - 无 ExifTool：exifread 解析 EXIF 标准字段 + `MakerNote TotalShutterReleases/MechanicalShutterCount`（Nikon）。
   - `read_exif_many(paths)`：批量，逐文件容错，坏文件返回错误标记不中断。
2. **`exif_viewer.py`（新模块）** — tkinter Toplevel 窗口：
   - 拖拽（tkinterdnd2，复用主窗口同款 DnD）或按钮选择文件/文件夹。
   - ttk.Treeview 表格：文件名 | 机身 | **快门数** | 快门速度 | 光圈 | ISO | 焦距 | 拍摄时间 | 镜头。
   - 状态栏进度；坏文件/无 EXIF 标红；快门数列高亮；双击行显示完整原始字段。
   - 后台线程解析，大目录不卡 UI。
3. **`photo_watermark.py` 改动（最小侵入）**：
   - 标题栏加「📷 EXIF」按钮 → `exif_viewer.open_exif_window(root, initial_paths)`。
   - 主窗口已选文件列表处加「发到 EXIF 查看」按钮。
4. **打包**：
   - `photo_watermark.spec`（完整版）：`datas` 加 `exiftool.exe`。
   - `photo_watermark_lite.spec`（精简版）：不含 exiftool.exe。
   - 复用现有 excludes 与 UPX 配置。

## 错误处理

- ExifTool 均找不到：窗口状态栏提示「未找到 ExifTool，使用精简解析（部分机身快门数可能读不到）」。
- 文件损坏/无 EXIF：该行红色显示原因，继续处理其余文件。
- 不支持格式：提示并跳过。

## 测试

- 单元测试（pytest 或内置断言脚本）覆盖 `read_exif`：NEF（快门数 571）、JPEG、无 ExifTool 回退路径、坏文件。
- 手工验收：NEF 单张、JPEG 单张、`101NZ7_2` 文件夹批量、两个 exe 各跑一遍。
