# -*- coding: utf-8 -*-
"""A/B 输出同区域裁剪对比图：多张图的同一 crop 纵向堆叠，带文件名标签

用法:
  python devtests/sr_ab_crop.py <输出.png> <x> <y> <w> <h> <图1> <图2> [...]
  crop 为第一张图坐标系（各图须同尺寸，2x JPG 口径）
"""
import sys
from pathlib import Path

import numpy as np
import cv2

ROW_W = 1400          # 每行统一宽度
LABEL_H = 44


def imread_rgb(path):
    buf = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f'无法读取: {path}')
    return img


def main():
    out_path = Path(sys.argv[1])
    x, y, w, h = (int(v) for v in sys.argv[2:6])
    rows = []
    for p in sys.argv[6:]:
        img = imread_rgb(p)
        crop = img[y:y + h, x:x + w]
        ch = int(crop.shape[0] * ROW_W / crop.shape[1])
        crop = cv2.resize(crop, (ROW_W, ch), interpolation=cv2.INTER_AREA)
        label = np.zeros((LABEL_H, ROW_W, 3), dtype=np.uint8)
        cv2.putText(label, Path(p).name, (12, 31), cv2.FONT_HERSHEY_SIMPLEX,
                    0.9, (255, 255, 255), 2, cv2.LINE_AA)
        rows += [label, crop, np.zeros((6, ROW_W, 3), dtype=np.uint8)]
    canvas = np.vstack(rows)
    cv2.imencode('.png', canvas)[1].tofile(str(out_path))
    print(f'{out_path}  ({canvas.shape[1]}x{canvas.shape[0]})')


if __name__ == '__main__':
    main()
