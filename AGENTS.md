# 协作约定（给 AI 助手的项目规则）

本文件是项目级的长期约定，开工前先读。详细技术说明见 `devdocs/`。

## 交付边界：只改源码，不发版

**默认只做到 `.py` 为止**——改代码、跑测试、本地 `git commit`。下面这些**由用户自己执行**，
除非他在当次对话里明确让你做：

- ❌ 不动 `VERSION = 'v2.1.2'`（`photo_watermark.py` 顶部）
- ❌ 不跑 PyInstaller、不生成/覆盖 `photo_watermark.exe` / `dist\*`
- ❌ 不 `git push`、不打 tag、不建 Release

用户的原话：*"你以后做到 py 就行了，我感觉效果不错就更新版本号 打包到 exe 然后推送 GitHub"*。

需要发版时，把命令列给他就行（见 `devdocs/构建与发布.md`）：

```bash
# 1. 升版本号：photo_watermark.py 顶部 VERSION
# 2. 打包
python -m PyInstaller photo_watermark.spec --noconfirm --clean
Copy-Item dist\photo_watermark.exe photo_watermark.exe -Force
# 3. 发布
git push origin main
git tag -a v2.1.3 -m "v2.1.3 ..." && git push origin v2.1.3
```

## 代码与测试

- 解释器：`py -3`（系统 Python 3.12，装了 rawpy/cv2/tifffile/piexif；仓库 `.venv` 里没有 rawpy）
- 提交前跑全量：`py -3 -m pytest -q`（当前 197 项）
- 注释、docstring、提交信息一律中文；提交信息写清"改了什么 + 为什么 + 实测数据"
- 新功能配单测，放在 `tests/`，用合成数据，不依赖真实 RAW

## 处理用户照片时的安全线

- 删除一律走**回收站**（`blur_cull.delete_files(recycle=True)`），不做永久删除
- 大批量删除前先 dry-run 打印清单和总大小
- 只读分析可以随便做；**写/删用户素材前，先确认路径是绝对路径且确实存在**
