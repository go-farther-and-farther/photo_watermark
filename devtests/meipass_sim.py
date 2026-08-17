# -*- coding: utf-8 -*-
"""冻结环境 _MEIPASS 分支模拟测试。

模拟 PyInstaller onefile 的解压目录布局（sys._MEIPASS），验证：
    1. find_exiftool() 在 _MEIPASS 存在时命中内置 ExifTool（优先级高于 PATH/常见安装位置）；
    2. 命中的"内置版"能真实解析 NEF 快门数。

解压目录用固定路径 + junction 指向本机 ExifTool 便携版，避免复制 ~37MB：

    _exe_t1/extract/exiftool -> %LOCALAPPDATA%\\Programs\\ExifTool   (junction)
        ├── ExifTool.exe          （启动器）
        └── exiftool_files\\      （Perl 引擎）

与 photo_watermark.spec 的 datas=('...\\ExifTool', 'exiftool') 布局一致。

用法: python devtests/meipass_sim.py [NEF文件...]
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EXTRACT = ROOT / '_exe_t1' / 'extract'
SAMPLE_NEF = r'D:\photo\raw\Z6_101\DSC_0299.NEF'


def main():
    targets = sys.argv[1:] or [SAMPLE_NEF]

    # 1. 校验模拟解压目录布局（exiftool/ExifTool.exe + exiftool_files/）
    launcher = EXTRACT / 'exiftool' / 'ExifTool.exe'
    engine_dir = EXTRACT / 'exiftool' / 'exiftool_files'
    assert launcher.is_file(), f'缺少内置 ExifTool 启动器: {launcher}'
    assert engine_dir.is_dir(), f'缺少 ExifTool 引擎目录: {engine_dir}'

    # 2. 模拟冻结环境：sys._MEIPASS 指向固定解压目录（等价于 exe 自解压后的临时目录）
    os.environ.pop('EXIFTOOL_PATH', None)   # 排除显式环境变量干扰，确保走 _MEIPASS 分支
    sys._MEIPASS = str(EXTRACT)

    import exif_reader
    found = exif_reader.find_exiftool()
    print(f'SIM found={found}')
    assert found is not None, 'find_exiftool 未命中内置版'
    assert str(Path(found).resolve()).lower() == str(launcher.resolve()).lower(), \
        f'命中 {found}，期望内置 {launcher}'

    # 3. 用"内置版"真实解析并校验快门数（基准: DSC_0299.NEF = NIKON Z 6 / 2219）
    for t in targets:
        r = exif_reader.read_exif_file(t, exiftool=found)
        print(f'SIM {Path(t).name}: ok={r["ok"]} engine={r["engine"]} '
              f'model={r["model"]!r} shutter={r["shutter_count"]} error={r["error"]!r}')
        assert r['ok'] and r['engine'] == 'exiftool', f'解析失败: {r}'

    if targets[0] == SAMPLE_NEF:
        first = exif_reader.read_exif_file(targets[0], exiftool=found)
        assert first['shutter_count'] == 2219, f'快门数不符: {first["shutter_count"]}'
        assert 'Z 6' in first['model'], f'型号不符: {first["model"]!r}'

    print('SIM done: _MEIPASS 分支命中内置 ExifTool 且解析正确')


if __name__ == '__main__':
    main()
