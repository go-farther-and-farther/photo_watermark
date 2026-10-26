# -*- coding: utf-8 -*-
"""给已有的 Linear DNG 重写更大的内嵌预览（Windows 资源管理器/照片应用看的就是这层）

背景：管线早期产物（及 Adobe 转换器式金字塔结构）内嵌预览只有 1600/1024 甚至
256 宽，资源管理器里点开像"尺寸大画质低"。本工具按管线口径重渲染一张大预览
（LR 口径：只 gamma 不拉伸），用管线标准结构整体重写文件。默认 raw 无压缩 +
预览无压缩（Windows 资源管理器/照片应用实测最稳；JXL 路线在这两个里打不开，
只有 LR 支持）。无损验证通过才替换，默认留 .bak。

色彩变换用 derive_camera_to_srgb（对 OriginalRawFileName 指到的参考 RAW 实测
仿射）——理论矩阵路线（ColorMatrix1 推导）实测强偏色，别用。找不到参考 RAW
就跳过该文件（--matrix-fallback 可强制走理论矩阵，仅救急）。

安全：默认先留 .bak 备份再原子替换；--dry-run 只打印计划。

用法：
    py -3 devtests/reembed_dng_preview.py <DNG 或目录> [--max-side 4096]
        [--ref-dir <RAW 素材目录>]   找参考帧用，默认 ~/Pictures/raw
        [--jxl-raw] [--lossy]        raw 走 JPEG XL（小体积；Windows 打不开，慎用）
        [--matrix-fallback] [--no-backup] [--dry-run]
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import cv2  # noqa: E402
import tifffile  # noqa: E402

import sr_process  # noqa: E402

# XYZ(D65) -> 线性 sRGB（仅 --matrix-fallback 救急用）
XYZ2SRGB = np.array([[3.2404542, -1.5371385, -0.4985314],
                     [-0.9692660, 1.8760108, 0.0415560],
                     [0.0556434, -0.2040259, 1.0572252]], np.float64)


def _et_read(et, tags, path):
    """exiftool -s3 逐标签取值，返回 dict。

    必须带 -f：缺标签时占位输出 '-'，保证行序与请求的标签一一对应——
    不带 -f 时缺标签直接少一行，按位置 zip 会整体错位（缺
    UniqueCameraModel 时相机型号会拿到 DNGVersion 的值，静默写错标签）。
    encoding='utf-8', errors='replace'：Windows 下避免 locale 解码 mojibake。"""
    r = subprocess.run([et, '-s3', '-f', *tags, str(path)],
                       capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=120)
    vals = [v.strip() for v in (r.stdout or '').splitlines()]
    return {t: ('' if v == '-' else v)
            for t, v in zip(tags, vals + ['-'] * (len(tags) - len(vals)))}


def _nums(s):
    return [float(v) for v in s.replace(',', ' ').split()]


def _find_ref_raw(name, ref_dirs):
    """按 OriginalRawFileName 在候选目录里找参考 RAW（含一层子目录）"""
    if not name:
        return None
    low = name.lower()
    for d in ref_dirs:
        d = Path(d).expanduser()
        if not d.is_dir():
            continue
        for p in [d / name] + [q for q in d.rglob(name) if q.is_file()]:
            if p.is_file() and p.suffix.lower() != '.dng':
                return p
        # 大小写不敏感兜底（一层子目录足够覆盖 raw\\<批次>\\ 布局）
        for sub in d.iterdir():
            if sub.is_dir():
                for p in sub.rglob(low):
                    if p.is_file() and p.suffix.lower() != '.dng':
                        return p
    return None


def render_preview(arr_u16, color_matrix, as_shot_neutral, max_side,
                   transform=None):
    """渲染内嵌预览（LR 口径：只 gamma 不拉伸，与管线 stretch=False 一致）

    transform 给出时走管线实测仿射（首选）；否则走 ColorMatrix1 理论矩阵
    （--matrix-fallback 救急，实测会偏色）。先按 max_side 降采样再渲染：
    统计量与全幅一致，内存省一个量级。
    """
    h, w = arr_u16.shape[:2]
    dw = min(max_side, w)
    dh = max(1, round(h * dw / w))
    small = cv2.resize(arr_u16, (dw, dh), interpolation=cv2.INTER_AREA)
    lin = small.astype(np.float32) / 65535.0
    if transform is not None:
        prev = sr_process.render_srgb_preview(lin, transform, stretch=False)
    else:
        wb = lin / np.asarray(as_shot_neutral, np.float32)
        cm = np.asarray(color_matrix, np.float64).reshape(3, 3)
        srgb = np.clip(wb.astype(np.float64) @ cm.T @ XYZ2SRGB.T, 0, 1)
        prev = (np.power(srgb, 1.0 / 2.2) * 255 + 0.5).astype(np.uint8)
    return np.ascontiguousarray(prev)


def process_one(et, dng, args, ref_dirs):
    tags = _et_read(et, ['-ColorMatrix1', '-AsShotNeutral', '-UniqueCameraModel',
                         '-DNGVersion', '-OriginalRawFileName'], dng)
    cm, asn = _nums(tags['-ColorMatrix1']), _nums(tags['-AsShotNeutral'])
    model = tags['-UniqueCameraModel'].strip()
    if len(cm) != 9 or len(asn) != 3 or not model:
        print(f'  [跳过] 元数据不全（ColorMatrix1/AsShotNeutral/UniqueCameraModel）')
        return False
    transform = None
    ref_raw = _find_ref_raw(tags['-OriginalRawFileName'].strip(), ref_dirs)
    if ref_raw is not None:
        transform = sr_process.derive_camera_to_srgb(ref_raw)
        src = f'参考帧实测仿射（{ref_raw.name}）'
    elif args.matrix_fallback:
        src = '理论矩阵（--matrix-fallback，实测会偏色，仅救急）'
    else:
        print(f'  [跳过] 找不到参考 RAW "{tags["-OriginalRawFileName"].strip()}"'
              f'（用 --ref-dir 指定素材目录，或 --matrix-fallback 强制理论矩阵）')
        return False
    arr = sr_process.load_subifd_rgb(dng)
    prev = render_preview(arr, cm, asn, args.max_side, transform=transform)
    old_mb = dng.stat().st_size / 1024 / 1024
    print(f'  色彩变换: {src}')

    if args.jxl_raw:
        comp, cargs, dver = ('jpegxl_dng',
                             {'lossless': False, 'distance': 0.5} if args.lossy else {'lossless': True},
                             '1.7.0.0')
    else:
        comp, cargs, dver = None, None, '1.4.0.0'

    tmp = dng.with_suffix('.tmp.dng')
    if not sr_process.write_linear_dng(
            tmp, arr, cm, asn, model, et, preview_rgb=prev,
            orig_raw_name=tags['-OriginalRawFileName'].strip() or None,
            compression=comp, compressionargs=cargs, dng_version=dver,
            preview_max_side=args.max_side):
        tmp.unlink(missing_ok=True)
        print(f'  [失败] 写入失败')
        return False
    # 验证：raw 像素逐位一致（无损路径）、预览尺寸正确
    back = sr_process.load_subifd_rgb(tmp)
    if args.lossy:
        if back.shape != arr.shape:
            tmp.unlink(missing_ok=True)
            print('  [失败] 读回尺寸不一致')
            return False
    elif not np.array_equal(back, arr):
        tmp.unlink(missing_ok=True)
        print('  [失败] 无损校验不过（像素变了）')
        return False
    new_mb = tmp.stat().st_size / 1024 / 1024
    print(f'  {dng.name}: {old_mb:.0f}MB -> {new_mb:.0f}MB, '
          f'预览 {prev.shape[1]}x{prev.shape[0]}, '
          f'raw={"JXL" if comp else "无压缩"}, 预览无压缩')
    if args.dry_run:
        tmp.unlink(missing_ok=True)
        return True
    if not args.no_backup:
        bak = dng.with_suffix(dng.suffix + '.bak')
        if not bak.exists():
            dng.replace(bak)
            tmp.replace(dng)
        else:
            tmp.replace(dng)
    else:
        tmp.replace(dng)
    return True


def main():
    ap = argparse.ArgumentParser(description='重写 Linear DNG 的内嵌预览（raw 数据不动）')
    ap.add_argument('target', help='DNG 文件或包含 DNG 的目录（不递归）')
    ap.add_argument('--max-side', type=int, default=4096, help='预览最长边（默认 4096）')
    ap.add_argument('--ref-dir', default='~/Pictures/raw',
                    help='找参考 RAW 的目录（默认 ~/Pictures/raw，含一层子目录）')
    ap.add_argument('--jxl-raw', action='store_true',
                    help='raw 用 JPEG XL 压缩（默认无压缩——2026-10-26 实测 JXL raw '
                         '在 Windows 资源管理器/照片应用打不开，只适合只用 LR 的流程）')
    ap.add_argument('--lossy', action='store_true',
                    help='配合 --jxl-raw：有损 distance 0.5（体积小，多一代损失）')
    ap.add_argument('--matrix-fallback', action='store_true',
                    help='找不到参考 RAW 时强制理论矩阵渲染（会偏色，仅救急）')
    ap.add_argument('--no-backup', action='store_true', help='不留 .bak（默认保留）')
    ap.add_argument('--dry-run', action='store_true', help='只打印计划，不写盘')
    args = ap.parse_args()

    from exif_reader import find_exiftool
    et = find_exiftool()
    if not et:
        print('exiftool 不可用')
        return 2
    ref_dirs = [args.ref_dir, str(Path(args.target).expanduser().parent)]
    root = Path(args.target)
    dngs = sorted(root.glob('*.dng')) if root.is_dir() else [root]
    dngs = [p for p in dngs if p.suffix.lower() == '.dng' and p.is_file()
            and not p.name.endswith(('.bak', '.tmp.dng'))]
    if not dngs:
        print('没有找到 DNG')
        return 1
    print(f'{len(dngs)} 个 DNG，预览最长边 {args.max_side}px'
          f'{"，DRY-RUN" if args.dry_run else ""}')
    n_ok = 0
    for p in dngs:
        try:
            n_ok += process_one(et, p, args, ref_dirs)
        except Exception as exc:                      # noqa: BLE001
            # 异常路径的 .tmp.dng 不清掉，下次会被当成目标 DNG 再处理，
            # 生成 x.tmp.tmp.dng 甚至把半成品覆盖到原文件
            p.with_suffix('.tmp.dng').unlink(missing_ok=True)
            print(f'  [失败] {p.name}: {exc}')
    print(f'完成 {n_ok}/{len(dngs)}')
    return 0 if n_ok == len(dngs) else 1


if __name__ == '__main__':
    sys.exit(main())
