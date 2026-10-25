# -*- coding: utf-8 -*-
"""清糊片窗口冒烟测试：构建 App、模拟判定结果填确认栏、勾选/删除联动、参数校验。

用法: py -3 devtests/blur_ui_smoke.py
不真扫描真照片（Frame 用假路径，缩略图线程会静默失败，不影响断言）。
"""
import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import blur_cull as bc  # noqa: E402
import blur_gui  # noqa: E402


def main():
    root = tk.Tk()
    root.withdraw()
    app = blur_gui.App(root)
    root.update_idletasks()

    ok = []

    # 1. 确认栏：糊帧默认勾、软帧默认不勾，删除按钮文案随勾选数变
    frames = []
    for i, dec in enumerate([bc.DEC_BLUR, bc.DEC_BLUR, bc.DEC_SOFT]):
        f = bc.Frame(path=Path(f'P10900{i}.RW2'))
        f.decision = dec
        f.rel = 0.7 if dec == bc.DEC_BLUR else 0.85
        f.shutter, f.iso = '1/20', '6400'
        frames.append(f)
    app._build_review(frames[:2], frames[2:])
    root.update_idletasks()
    ok.append(('行数 = 糊+软', len(app._rows) == 3))
    ok.append(('默认勾选 = 2 张糊', len(app._checked_frames()) == 2))
    ok.append(('按钮文案带勾选数', '勾选 2 张' in app.btn_del.cget('text')))

    app._rows[0]['var'].set(False)
    ok.append(('取消勾选联动按钮', '勾选 1 张' in app.btn_del.cget('text')))
    app._rows[2]['var'].set(True)                        # 软帧勾上也能删
    checked = app._checked_frames()
    ok.append(('软帧可勾选', len(checked) == 2
               and any(f.decision == bc.DEC_SOFT for f in checked)))

    # 2. 空确认栏：按钮禁用、标题提示
    app._build_review([], [])
    ok.append(('无糊软帧时按钮禁用', str(app.btn_del.cget('state')) == 'disabled'))

    # 3. 参数校验：越界删除线 / 非数字间隔 都要被拦住且不进入扫描
    popped = []
    orig = blur_gui.messagebox.showwarning
    blur_gui.messagebox.showwarning = lambda *a, **k: popped.append(a)
    try:
        app.ratio.set('1.5')
        app._scan()
        ok.append(('越界删除线被拦', len(popped) == 1 and not app.busy))
        app.ratio.set('0.80')
        popped.clear()
        app.gap.set('abc')
        app._scan()
        ok.append(('非数字组间隔被拦', len(popped) == 1 and not app.busy))
    finally:
        blur_gui.messagebox.showwarning = orig

    for name, passed in ok:
        print(('PASS ' if passed else 'FAIL ') + name)
    root.destroy()
    bad = [n for n, p in ok if not p]
    print('SMOKE blur_gui:', 'OK' if not bad else f'FAILED: {bad}')
    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main())
