# -*- coding: utf-8 -*-
"""试跑指定连拍组：判定 → 合成 → 出宣传对比图

用法: python devtests/sr_promo_one.py <DSC_xxxx.NEF 的完整路径>
      会自动取同目录里与它同一连拍（间隔 ≤2s）的所有 NEF。
"""
import sys
import time
from pathlib import Path

import numpy as np
import cv2

sys.path.insert(0, r'D:\Software files\photo_watermark')
sys.path.insert(0, r'D:\Software files\photo_watermark\devtests')
import sr_suggest
import sr_process
import sr_promo


def burst_of(p, gap=2.0):
    """从给定文件出发，向两侧扩展到同一连拍（EXIF 时间间隔 ≤ gap 秒）"""
    folder = p.parent
    etc = sorted(folder.glob('*.NEF'))
    ts = {}
    for f in etc:
        r = sr_suggest._read_timestamp(f)
        if r:
            ts[f] = r[0]
    if p not in ts:
        return [p]
    i = etc.index(p)
    lo = i
    while lo > 0 and ts[etc[lo]] - ts[etc[lo - 1]] <= gap:
        lo -= 1
    hi = i
    while hi < len(etc) - 1 and ts[etc[hi + 1]] - ts[etc[hi]] <= gap:
        hi += 1
    return etc[lo:hi + 1]


def main():
    p = Path(sys.argv[1])
    files = burst_of(p)
    print(f'连拍 {len(files)} 帧：{files[0].name} .. {files[-1].name}')
    v = sr_suggest.judge_group(files, start_time=sr_suggest._read_timestamp(files[0])[0])
    print('判定', v.status, '|', v.reason)

    tag = f'组_{files[0].stem}_{time.strftime("%m%d%H%M", time.localtime(v.start_time))}'
    out_dir = sr_promo.OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    base = out_dir / tag
    jpg = Path(str(base) + '_2x.jpg')
    if not jpg.exists():
        t0 = time.time()
        sr_process.process(files=[str(f) for f in files],
                           output_path=str(base) + '.tif', scale=2,
                           max_frames=16, threads=8)
        print(f'合成耗时 {time.time()-t0:.0f}s')
    sr_u8 = cv2.imdecode(np.fromfile(str(jpg), dtype=np.uint8),
                         cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    print('合成结果', sr_u8.shape)

    # 单帧（最锐）走同一色调映射
    sharp = []
    for f in files:
        g = sr_process.read_raw_as_gray(f, half_size=True)
        sharp.append(float((g - cv2.GaussianBlur(g, (0, 0), 2.0)).std()))
    best = files[int(np.argmax(sharp))]
    lin = sr_process.read_raw_camera_linear(best)
    A, b = sr_process.derive_camera_to_srgb(best) or (np.eye(3), np.zeros(3))
    lin2 = cv2.resize(np.clip(lin, 0, 1), None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    one_u8 = sr_process.render_srgb_preview(lin2.astype(np.float32), (A, b),
                                            stretch=True)
    one_u8 = cv2.cvtColor(one_u8, cv2.COLOR_RGB2BGR)
    del lin, lin2
    o = sr_promo._exif_orientation(best)
    if o != 1:
        sr_u8 = sr_promo._orient(sr_u8, o)      # 管线像素已正立，此处仅防御
    print('单帧', one_u8.shape, '最锐', best.name)

    g = dict(files=[str(f) for f in files], start=v.start_time,
             dir=str(p.parent), metrics=v.metrics)
    sr_promo.layout_one(0, g, tag, sr_u8, one_u8)


if __name__ == '__main__':
    main()
