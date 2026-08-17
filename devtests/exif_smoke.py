# -*- coding: utf-8 -*-
"""EXIF 窗口 GUI 冒烟测试：自动打开窗口 → 解析文件夹 → 校验行数 → 自动关闭。

用法: python devtests/exif_smoke.py [要解析的路径...]
默认解析 D:\\photo\\raw\\Z30_101（传参可覆盖为任意文件/文件夹）。
等待策略：每 500ms 轮询表格行数，行数连续 2s 不变且非空即视为解析完成（上限 60s），
因此对大文件夹也适用。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import exif_viewer  # noqa: E402


def main():
    from tkinter import ttk
    targets = sys.argv[1:] or [r'D:\photo\raw\Z30_101']
    win = exif_viewer.open_exif_window(parent=None, initial_paths=targets)

    def find_tree(w):
        for c in w.winfo_children():
            if isinstance(c, ttk.Treeview):
                return c
            hit = find_tree(c)
            if hit:
                return hit
        return None

    last_n, stable, deadline = -1, 0, 120   # 500ms × 120 = 60s 上限

    def check():
        nonlocal last_n, stable, deadline
        tv = find_tree(win)
        rows = tv.get_children() if tv else []
        n = len(rows)
        if n == last_n and n > 0:
            stable += 1
        else:
            stable, last_n = 0, n
        deadline -= 1
        if (n > 0 and stable >= 4) or deadline <= 0:
            print(f'SMOKE rows={n} timeout={deadline <= 0}')
            if rows:
                print(f'SMOKE first={tv.item(rows[0], "values")}')
                print(f'SMOKE last={tv.item(rows[-1], "values")}')
            win.destroy()
            win.quit()
        else:
            win.after(500, check)

    win.after(2000, check)   # 先给窗口初始化与解析启动留 2s
    win.mainloop()
    print('SMOKE done')


if __name__ == '__main__':
    main()
