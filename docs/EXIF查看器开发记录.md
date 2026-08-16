# EXIF 查看器（快门数）开发记录

> 记录时间：2026-08-16 · 会话交接文档
> 需求：本地读取照片 EXIF（重点：**快门数**），不再上传网页查询；集成进 photo_watermark 水印工具，打两个包。

---

## 1. 方案（已按此实现）

- **一份代码，两套打包**，EXIF 读取引擎按优先级自动选择：
  `内置 ExifTool → 系统安装的 ExifTool → exifread（纯 Python 回退）`
- **完整版** `photo_watermark.exe`（~36MB）：内置整个 ExifTool 便携目录，任何品牌/格式（NEF/ARW/CR3…）都能可靠读快门数
- **精简版** `photo_watermark_lite.exe`（~23MB）：不内置 ExifTool，exifread 解析；Nikon 多数机身可读快门数；本机已装 ExifTool 时也会自动调用
- **集成方式**（方案 A）：主窗口标题栏新增「📷 EXIF」按钮 + 选图区「查看EXIF」按钮 → 打开独立 EXIF 窗口，主界面布局零改动
- 版本号升至 **v1.7.0**

## 2. 交付文件

| 文件 | 说明 |
|---|---|
| `exif_reader.py` | 核心解析模块（纯函数，可单测）：`find_exiftool()` / `read_exif_file()` / `read_exif_batch()` |
| `exif_viewer.py` | EXIF 查看窗口（tkinter Toplevel）：拖拽/选文件/文件夹、表格、后台线程、双击详情；可独立运行 `python exif_viewer.py` |
| `photo_watermark.py` | 主程序改动：v1.7.0、导入 exif_viewer、标题栏/选图区两个按钮 |
| `tests/test_exif_reader.py` | 10 个单元测试（exiftool + exifread 双引擎、批量、坏文件、文件夹） |
| `pytest.ini` | `-p no:cacheprovider`（沙箱下 pytest 缓存目录写不了） |
| `devtests/exif_smoke.py` | EXIF 窗口 GUI 冒烟测试（自动解析文件夹→校验行数→自关） |
| `devtests/main_window_smoke.py` | 主窗口集成冒烟测试（自动点「📷 EXIF」按钮→校验 EXIF 窗口弹出） |
| `photo_watermark.spec` | 完整版打包配置：`datas` 增加整个 ExifTool 目录 |
| `photo_watermark_lite.spec` | 精简版打包配置（无 exiftool） |
| `docs/superpowers/specs/2026-08-16-exif-viewer-design.md` | 设计文档 |

## 3. 验证结果

### ✅ 已完成并确认

1. **单元测试**：`python -m pytest -q` → **10 passed**
   - ExifTool 引擎读 NEF：快门 571、Z 7_2、F4、1/8000s、ISO 64、33mm、镜头全对
   - exifread 引擎（无 ExifTool 回退）：同样全部读到（含 `TotalShutterReleases=571`）
   - JPEG（demo_photo.jpg）：NIKON Z 7、F5.6、1/200s，快门数显示"—"（JPEG 无此字段，正常）
   - 坏文件/文件夹/缺失文件 → 标红错误不中断；批量进度回调正常
2. **EXIF 窗口冒烟**：`python devtests/exif_smoke.py` → 解析 `D:\photo\raw\101NZ7_2` 全 19 个 NEF，首行 DSC_0501 快门 505，末行 DSC_0567 快门 571（与直接 exiftool 查的一致）
3. **主窗口集成冒烟**：`python devtests/main_window_smoke.py` → 标题栏「📷 EXIF」按钮 ✓、选图区「查看EXIF」按钮 ✓、点击后 EXIF 窗口成功弹出 ✓
4. **两个 exe 构建成功**：完整版 36.4MB / 精简版 22.8MB（`dist/`）
5. **完整版内置 exiftool 可用性**：从 exe 包内解出 553 个文件（`exiftool\ExifTool.exe` + `exiftool_files\` 全树），运行 `-ver` → 13.59，读 DSC_0567.NEF → `ShutterCount: 571` ✓
6. **完整版 exe 启动**：`dist\photo_watermark.exe` 启动正常，主窗口 "Photo Watermark - 选择照片" 出现，OCR 确认「📷 EXIF」「查看EXIF」按钮已渲染
7. **git 已提交**（5 个 commit）：设计文档 → 功能 v1.7.0 → README ×2 → spec ×2

### ⚠️ 尚未完成（下次会话可补）

1. **打包后 exe 内点击「📷 EXIF」的端到端验证**：沙箱/前台/坐标问题导致无法用脚本点击（环境问题，非产品缺陷）。用户手工验证：双击 exe → 点「📷 EXIF」→ 拖入 NEF 文件夹 → 应显示 19 行、快门数 505–571、右上角引擎标注「ExifTool 完整解析」
2. **精简版 exe 启动冒烟**：还没启动过 `photo_watermark_lite.exe`（应正常启动；本机因装有系统 ExifTool，其 EXIF 窗口会显示「ExifTool 完整解析」——这是设计行为，exifread 回退路径已由单元测试覆盖）
3. **冻结环境 `_MEIPASS` 分支模拟测试**：脚本已就绪（设置 `sys._MEIPASS` 指向解压目录后调 `find_exiftool` 应命中内置版），但沙箱每个进程的临时目录不同导致解压目录找不到，下次用固定路径（如 `D:\Software files\photo_watermark\_exe_t1\extract\`）重跑

## 4. 使用方法（用户视角）

1. 打开 exe → 主窗口右上角「📷 EXIF」或选图区「查看EXIF」
2. EXIF 窗口：把 NEF/JPEG/文件夹直接拖进去，或点「选择文件/文件夹」
3. 表格显示：文件名 | 机身 | **快门数** | 快门速度 | 光圈 | ISO | 焦距 | 拍摄时间 | 镜头
4. 红色行 = 解析失败（双击看原因）；双击任意行看完整信息；右上角显示当前解析引擎

## 5. 构建命令

```powershell
# 完整版（内置 ExifTool）
python -m PyInstaller --noconfirm photo_watermark.spec

# 精简版（无 ExifTool）
python -m PyInstaller --noconfirm photo_watermark_lite.spec
```

产物在 `dist\`。完整版依赖本机 ExifTool 便携目录（`%LOCALAPPDATA%\Programs\ExifTool\`，含 `ExifTool.exe` 启动器 + `exiftool_files\` 引擎，当前 13.59 版）。

## 6. 测试命令

```powershell
python -m pytest -q                     # 单元测试（10 个）
python devtests/exif_smoke.py           # EXIF 窗口冒烟（默认解析 101NZ7_2 文件夹）
python devtests/main_window_smoke.py    # 主窗口集成冒烟（自动点 EXIF 按钮）
```

## 7. 关键技术点 / 踩坑记录

1. **ExifTool 便携版不是单文件**：`ExifTool.exe`（58KB）只是启动器，真正的 Perl 引擎在旁边的 `exiftool_files\`（~37MB，含各格式 .pm 模块、.xs.dll、Geolocation.dat）。**打包必须带整个目录**，只打包启动器会报 `Could not find ...\exiftool_files\perl5*.dll`。
2. **exiftool 对无法识别文件的 stderr 行为不稳定**（pwsh 直调有提示、python 子进程里为空、退出码恒 0）→ 解析模块改用**文件头魔数**（JPEG `FFD8FF` / TIFF 系 `II*\0` 等）判断"合法图片无 EXIF"与"不是图片"，见 `_looks_like_image()`。
3. **exifread 的 `details=False` 会跳过 Nikon MakerNote**（含快门数）→ 必须用默认 `details=True`。
4. **exifread 快门数标签**：`MakerNote TotalShutterReleases`（含电子快门）优先，其次 `MechanicalShutterCount`。
5. **沙箱环境限制（仅影响本开发环境的自动化，用户正常使用不受影响）**：
   - 子进程**禁止删除**文件/目录、禁止在系统临时目录建嵌套目录 → 单元测试不用 tmp_path（改用仓库现有文件模拟坏文件）；pytest 关掉 cacheprovider；打包后 exe 在本沙箱里无法解压启动，需完整权限或在真实环境双击
   - 打包后 exe 测试可在 `danger-full-access` 权限下运行
6. **tkinter 按钮无法通过 UI Automation 找到**（Tk 的辅助功能桥不暴露按钮），PostMessage 合成鼠标消息 Tk 也不响应 → 打包版 GUI 点击验证留给用户手工做。
7. **表格快门数列高亮**：ttk.Treeview 只支持整行 tag（错误行红色），快门列本身不单独加色——列为独立"快门数"列，信息已足够清晰。

## 8. 下一步建议

- [ ] 用户手工验收两个 exe（完整版拖 NEF 文件夹；精简版装/不装 ExifTool 各试一次）
- [ ] 精简版 exe 启动冒烟
- [ ] 补 `_MEIPASS` 分支模拟测试（固定解压路径）
- [ ] 如要发布，把 exiftool 目录的许可说明（ExifTool 为 Artistic/GPL 双许可，可再分发）写进 README
