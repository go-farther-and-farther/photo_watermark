# -*- coding: utf-8 -*-
"""四格对比窗口冒烟测试：构建 App、模拟加/删裁切点与参数校验，不进 mainloop。

用法: py -3 devtests/quad_ui_smoke.py
验证窗口能建起来、裁切点交互与校验逻辑可用（不真生成图）。
"""
import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import quad_gui  # noqa: E402
import quad_compare as qc  # noqa: E402


def main():
    root = tk.Tk()
    root.withdraw()
    app = quad_gui.App(root)
    root.update_idletasks()

    ok = []

    # 1. 裁切点：预览未载入时点画布不应崩；载入合成预览后点击应加进列表
    def _ev(x, y):
        return type('Ev', (), {'x': x, 'y': y})()

    app._on_click(_ev(100, 100))
    ok.append(('未载入预览时点击不崩', len(app.spots) == 0))

    import numpy as np
    img = np.zeros((300, 480, 3), np.uint8)
    img[:] = (40, 60, 80)
    app._show_preview(img)
    root.update_idletasks()
    app.spot_name.set('测试点')
    app._on_click(_ev(app._disp[2] + 120, app._disp[3] + 90))
    ok.append(('点击加裁切点', len(app.spots) == 1 and app.spots[0][0] == '测试点'))
    ok.append(('相对坐标换算', abs(app.spots[0][1] - 120 / 480) < 1e-6
               and abs(app.spots[0][2] - 90 / 300) < 1e-6))
    ok.append(('列表同步', app.spot_list.get(0).startswith('测试点')))

    # 2. 删除点
    app.spot_list.selection_set(0)
    app._del_spot()
    ok.append(('删除点', len(app.spots) == 0 and app.spot_list.size() == 0))

    # 3. 校验：无参考图 / 非图片参考图 都应弹警告并返回 None（messagebox 打桩）
    popped = []
    import quad_gui as qg
    orig = qg.messagebox.showwarning
    qg.messagebox.showwarning = lambda *a, **k: popped.append(a)
    try:
        ok.append(('无参数校验拦截', app._collect() is None and len(popped) >= 1))
        app.ref.set(str(Path(qc.__file__).parent / 'quad_compare.py'))  # 存在但非图片
        popped.clear()
        ok.append(('非图片参考图拦截读预览', app._collect() is None))
    finally:
        qg.messagebox.showwarning = orig

    for name, passed in ok:
        print(('PASS ' if passed else 'FAIL ') + name)
    root.destroy()
    bad = [n for n, p in ok if not p]
    print('SMOKE quad_gui:', 'OK' if not bad else f'FAILED: {bad}')
    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main())
