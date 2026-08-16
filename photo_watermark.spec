# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['photo_watermark.py'],
    pathex=[],
    binaries=[],
    # 内嵌品牌Logo、默认配置与演示照片：单独发 exe 也能用（exe 旁有同名文件时优先用外面的，可自定义）
    datas=[('logos', 'logos'), ('水印设置.ini', '.'), ('input/demo_photo.jpg', 'input')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'pandas', 'scipy', 'torch', 'torchvision', 'tensorflow', 'keras', 'matplotlib', 'pygame', 'nltk', 'cryptography', 'sklearn', 'IPython', 'sympy', 'cv2'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='photo_watermark',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
