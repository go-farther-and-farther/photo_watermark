# -*- coding: utf-8 -*-
"""
VR/IBIS 防抖 A/B 测试：同一场景手持拍两组，一组开防抖、一组关防抖，
用无参考清晰度指标逐张评分并分组统计，判断防抖是否实际生效。

原理：同一静态场景、同参数曝光下，画面模糊（抖动）会直接压低高频梯度能量。
因此逐张计算：
    1. LapVar    拉普拉斯方差（经典模糊度量，越糊越小）
    2. Tenengrad Sobel 梯度平方均值
    3. Brenner   相距2像素灰度差平方均值
    4. LapVar2x  先 2x2 均值降采样再算拉普拉斯方差（压制像素级噪声干扰）
两组唯一预期差异是手抖模糊，指标系统性偏低的一组即更糊。

用法：python vr_sharpness_test.py [文件夹] [--off 起:止] [--on 起:止]
编号按文件名里 DSC_ 后的 4 位数字；--off/--on 为闭区间，如 --off 200:213 --on 214:245。
不传时默认按上一轮测试分组（178–188 开、189–199 关）。
"""
import argparse
import csv
import re
import sys
from pathlib import Path

import cv2
import exifread
import numpy as np
import rawpy
from scipy import stats

METRIC_NAMES = ["LapVar", "Tenengrad", "Brenner", "LapVar2x"]
CROP = 480  # 100% 对比裁切边长
FOLDER = OUT_DIR = None  # main() 里按命令行参数填充
ON_RANGE = OFF_RANGE = range(0)


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default=r"C:\Users\LQL\Pictures\raw\100NCZ_6")
    ap.add_argument("--on", default="178:188", help="开防抖编号区间 起:止（闭区间）")
    ap.add_argument("--off", default="189:199", help="关防抖编号区间 起:止（闭区间）")
    return ap.parse_args()


def parse_range(s: str) -> range:
    lo, hi = s.split(":")
    return range(int(lo), int(hi) + 1)


def discover_files(folder: Path) -> dict:
    """按文件名里的 4 位编号归组，兼容 DSC_0178.NEF / DSC_0178-261017.JPG。"""
    found = {}
    for p in sorted(folder.iterdir()):
        m = re.match(r"DSC_(\d{4})", p.name)
        if m and p.suffix.lower() in (".nef", ".jpg"):
            found[int(m.group(1))] = p
    return found


def read_exif(path: Path) -> dict:
    tags = exifread.process_file(open(path, "rb"), details=False)
    def g(name):
        t = tags.get(name)
        return str(t) if t else "-"
    return {
        "shutter": g("EXIF ExposureTime"),
        "iso": g("EXIF ISOSpeedRatings"),
        "aperture": g("EXIF FNumber"),
        "focal": g("EXIF FocalLength"),
    }


def imread_unicode(path: Path) -> np.ndarray:
    """OpenCV 在 Windows 下不支持中文路径，走字节流读写。"""
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise IOError(f"无法读取图片: {path}")
    return img


def imwrite_unicode(path: Path, img: np.ndarray):
    ext = path.suffix or ".png"
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        raise IOError(f"编码失败: {path}")
    buf.tofile(str(path))


def load_gray(path: Path) -> np.ndarray:
    if path.suffix.lower() == ".nef":
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=True, output_bps=8)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float64)
    return cv2.cvtColor(imread_unicode(path), cv2.COLOR_BGR2GRAY).astype(np.float64)


def sharpness(gray: np.ndarray) -> list:
    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F, ksize=3).var())
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    tenengrad = float(np.mean(gx * gx + gy * gy))
    diff = np.zeros_like(gray, dtype=np.float64)
    diff[:, :-2] = gray[:, 2:] - gray[:, :-2]
    brenner = float(np.mean(diff * diff))
    small = cv2.blur(gray, (2, 2))[::2, ::2]
    lap_var2x = float(cv2.Laplacian(small, cv2.CV_64F, ksize=3).var())
    return [lap_var, tenengrad, brenner, lap_var2x]


def best_crop_center(ref_gray: np.ndarray) -> tuple:
    """在参考帧上按块梯度能量找细节最丰富的区域，用于 100% 对比裁切。"""
    h, w = ref_gray.shape
    block, stride = 512, 128
    gx = cv2.Sobel(ref_gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(ref_gray, cv2.CV_64F, 0, 1, ksize=3)
    energy = np.abs(gx) + np.abs(gy)
    best, center = -1.0, (h // 2, w // 2)
    for y in range(block // 2, h - block // 2, stride):
        for x in range(block // 2, w - block // 2, stride):
            e = float(energy[y - block // 2:y + block // 2, x - block // 2:x + block // 2].mean())
            if e > best:
                best, center = e, (y, x)
    return center


def save_crops(rows: list, crop_center: tuple):
    """导出各组最好/中位帧的 100% 裁切与拼图，供肉眼复核。"""
    OUT_DIR.mkdir(exist_ok=True)
    y, x = crop_center
    half = CROP // 2
    picks = {}
    for g in ("ON", "OFF"):
        grp = sorted((r for r in rows if r["group"] == g), key=lambda r: r["m"][0])
        picks[f"VR_{g}_best"] = grp[-1]
        picks[f"VR_{g}_median"] = grp[len(grp) // 2]
    tiles = []
    for key, r in picks.items():
        gray = load_gray(r["path"])
        crop = np.clip(gray[y - half:y + half, x - half:x + half], 0, 255).astype(np.uint8)
        imwrite_unicode(OUT_DIR / f"crop_{key}.png", crop)
        tile = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        cv2.putText(tile, f"{key.replace('_', ' ')} {r['name']}", (10, 34),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2, cv2.LINE_AA)
        tiles.append(tile)
    imwrite_unicode(OUT_DIR / "对比拼图_2x2.png",
                    np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:])]))


def main():
    global FOLDER, ON_RANGE, OFF_RANGE, OUT_DIR
    args = parse_args()
    FOLDER = Path(args.folder)
    ON_RANGE, OFF_RANGE = parse_range(args.on), parse_range(args.off)
    found = discover_files(FOLDER)
    order = [n for n in sorted(found) if n in ON_RANGE or n in OFF_RANGE]
    OUT_DIR = FOLDER / f"防抖测试结果_{order[0]}-{order[-1]}"
    rows = []
    for num in order:
        path, group = found[num], ("ON" if num in ON_RANGE else "OFF")
        exif = read_exif(path)
        gray = load_gray(path)
        m = sharpness(gray)
        rows.append({"path": path, "name": path.stem, "group": group,
                     "exif": exif, "m": m, "brightness": float(gray.mean())})
        print(f"{path.stem} [VR {group}]  快门{exif['shutter']:>6} ISO{exif['iso']:>5} "
              f"f/{exif['aperture']:>4} {exif['focal']:>6}  "
              + "  ".join(f"{n}={v:.1f}" for n, v in zip(METRIC_NAMES, m)))

    print("\n===== 分组统计（主指标 LapVar，其余指标用于交叉验证）=====")
    summary = {}
    for g in ["ON", "OFF"]:
        vals = np.array([r["m"] for r in rows if r["group"] == g])
        summary[g] = {n: vals[:, i] for i, n in enumerate(METRIC_NAMES)}
        line = f"VR {g:>4}: "
        line += "  ".join(f"{n} 中位={np.median(v):.1f} 均值={v.mean():.1f} "
                          f"最差={v.min():.1f} 最好={v.max():.1f}" for n, v in summary[g].items())
        print(line)

    print("\n===== 显著性检验（Mann-Whitney U，双尾）=====")
    for i, n in enumerate(METRIC_NAMES):
        a = summary["ON"][n]
        b = summary["OFF"][n]
        u, p = stats.mannwhitneyu(a, b, alternative="two-sided")
        ratio = np.median(a) / np.median(b)
        print(f"{n:>9}: 开/关 中位数比 = {ratio:.2f}×   p = {p:.4f}   "
              f"{'差异显著' if p < 0.05 else '差异不显著'}")

    on_rows = [r for r in rows if r["group"] == "ON"]
    off_rows = [r for r in rows if r["group"] == "OFF"]
    print(f"\n开防抖组最差帧 LapVar = {min(r['m'][0] for r in on_rows):.1f}")
    print(f"关防抖组最好帧 LapVar = {max(r['m'][0] for r in off_rows):.1f}")
    bright = {r["name"]: r["brightness"] for r in rows}
    print(f"亮度范围 {min(bright.values()):.1f}–{max(bright.values()):.1f} "
          f"（应接近，否则曝光不一致需注意）")

    OUT_DIR.mkdir(exist_ok=True)
    with open(OUT_DIR / "结果.csv", "w", newline="", encoding="utf-8-sig") as f:
        wcsv = csv.writer(f)
        wcsv.writerow(["文件", "防抖", "快门", "ISO", "光圈", "焦距", "亮度"] + METRIC_NAMES)
        for r in rows:
            e = r["exif"]
            wcsv.writerow([r["name"], r["group"], e["shutter"], e["iso"],
                           e["aperture"], e["focal"], f"{r['brightness']:.1f}"]
                          + [f"{v:.1f}" for v in r["m"]])

    ref_name = max(rows, key=lambda r: r["m"][0])
    ref_gray = load_gray(ref_name["path"])
    save_crops(rows, best_crop_center(ref_gray))
    print(f"\n100% 裁切对比图与 CSV 已保存到：{OUT_DIR}")


if __name__ == "__main__":
    main()
