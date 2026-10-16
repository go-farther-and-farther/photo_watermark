# -*- coding: utf-8 -*-
"""sr_suggest + sr_gui 端到端冒烟：

合成一个混合文件夹（正常连拍 + 三脚架连拍 + 散照），走 GUI 的
扫描判定 → 默认勾选可超分组 → 合成 → 重扫验证「已完成」防重复。

用法: python devtests/sr_suggest_smoke.py
"""
import queue
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

cv2 = __import__('cv2') if True else None  # noqa: E402
piexif = __import__('piexif')  # noqa: E402

import sr_gui  # noqa: E402
from sr_suggest import scan_and_judge, load_done_registry  # noqa: E402


def _base(w=320, h=240, seed=7):
    rng = np.random.default_rng(seed)
    x, y = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    img = 0.5 + 0.25 * np.sin(x * 0.31 + y * 0.17) + 0.15 * np.sin(x * 0.83 - y * 0.51)
    img += rng.normal(0, 0.02, img.shape)
    return np.clip(img, 0.05, 0.95).astype(np.float32)


def _warp(img, dx, dy):
    h, w = img.shape[:2]
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REFLECT_101)


def _jpg(path, img, sec):
    frame = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    ok, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    Path(path).write_bytes(buf.tobytes())
    exif = piexif.dump({"Exif": {piexif.ExifIFD.DateTimeOriginal: f"2026:10:16 09:00:{sec:02d}".encode()}})
    piexif.insert(exif, str(path))


def wait_pred(q, pred, timeout=90):
    """取队列消息直到 pred 命中；返回是否命中（字符串消息即时打印便于排障）"""
    deadline = time.time() + timeout
    seen = []
    while time.time() < deadline:
        try:
            msg = q.get(timeout=0.5)
        except queue.Empty:
            continue
        seen.append(msg)
        if isinstance(msg, str) and msg != '__DONE__':
            print('  |', msg[:300])
        if msg == '__DONE__':
            return True
        if isinstance(msg, tuple) and pred(msg):
            return msg
    print('SMOKE 最近消息:', seen[-5:])
    return False


def main():
    tmp = Path(tempfile.mkdtemp(prefix='sr_smoke_'))
    src, dst = tmp / 'photos', tmp / 'out'
    src.mkdir()

    base = _base()
    for i, (dx, dy) in enumerate([(0, 0), (0.35, 0.6), (0.7, 0.1), (1.2, 1.5), (-0.5, 0.8), (0.2, -0.7)]):
        _jpg(src / f"good_{i:02d}.jpg", _warp(base, dx, dy), i)
    for i in range(4):
        _jpg(src / f"tripod_{i:02d}.jpg", base, 20 + i)
    _jpg(src / "pair_0.jpg", base, 40)
    _jpg(src / "pair_1.jpg", _warp(base, 0.4, 0.2), 41)

    root = tk.Tk()
    root.withdraw()
    app = sr_gui.App(root)
    app.var_src.set(str(src))
    app.var_dst.set(str(dst))

    # --- ① 扫描并判定 ---
    app._detect()
    r = wait_pred(app.q, lambda m: m[0] == '__VERDICTS__')
    assert isinstance(r, tuple), '扫描未在时限内完成'
    verdicts, singles, cancelled = r[1], r[2], r[3]
    statuses = sorted(v.status for v in verdicts)
    print('SMOKE 判定:', statuses, '散照:', singles, '取消:', cancelled)
    assert statuses == ['denoise_only', 'ok'] and singles == 2, statuses
    app._populate(verdicts, singles, cancelled)
    ok_gi = next(gi for gi, v in enumerate(verdicts, 1) if v.status == 'ok')
    print('SMOKE 默认勾选:', sorted(app.checked), '期望含组', ok_gi)
    assert ok_gi in app.checked
    wait_pred(app.q, lambda m: False, timeout=30)  # 排空扫描阶段的 __DONE__

    # --- ② 合成选中组 ---
    app._run()
    assert wait_pred(app.q, lambda m: False), '合成未在时限内完成'
    jpgs = list(dst.glob('*_2x.jpg'))
    print('SMOKE 输出:', [p.name for p in jpgs], '清单存在:', (dst / '.sr_done.json').exists())
    assert len(jpgs) == 1 and (dst / '.sr_done.json').exists()

    # --- ③ 重扫：应显示已完成 ---
    v2, s2 = scan_and_judge(src, output_dir=dst)
    statuses2 = sorted(v.status for v in v2)
    print('SMOKE 重扫:', statuses2)
    assert statuses2 == ['denoise_only', 'done'], statuses2
    reg = load_done_registry(dst)
    assert len(reg) == 1

    root.destroy()
    print('SMOKE done: 判定/勾选合成/防重复 全部通过')


if __name__ == '__main__':
    main()
