# -*- coding: utf-8 -*-
"""EXIF 窗口 GUI 冒烟测试：自动打开窗口 → 解析文件夹 → 校验行数 → 自动关闭。

用法: python devtests/exif_smoke.py [要解析的路径...]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import exif_viewer  # noqa: E402


def main():
    from tkinter import ttk
    targets = sys.argv[1:] or [r'D:\photo\raw\101NZ7_2']
    win = exif_viewer.open_exif_window(parent=None, initial_paths=targets)

    def find_tree(w):
        for c in w.winfo_children():
            if isinstance(c, ttk.Treeview):
                return c
            hit = find_tree(c)
            if hit:
                return hit
        return None

    def check():
        tv = find_tree(win)
        rows = tv.get_children() if tv else []
        print(f'SMOKE rows={len(rows)}')
        if rows:
            print(f'SMOKE first={tv.item(rows[0], "values")}')
            print(f'SMOKE last={tv.item(rows[-1], "values")}')
        win.destroy()
        win.quit()

    win.after(6000, check)
    win.mainloop()
    print('SMOKE done')


if __name__ == '__main__':
    main()
