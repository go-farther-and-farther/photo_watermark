# -*- coding: utf-8 -*-
"""压缩功能 UI 冒烟测试：
1) 主选择窗口样式列表出现「无水印(仅压缩)」勾选框
2) 设置窗口（总设置→输出设置）出现「最大边长 / 目标体积(MB) / 压缩输出子文件夹」三个控件

用法: python devtests/compress_ui_smoke.py
"""
import sys
import tkinter as tk
from tkinter import ttk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinterdnd2  # noqa: E402
import photo_watermark as pw  # noqa: E402


def find(widget, classes, text=None):
    """递归找第一个匹配类型（可选匹配文本）的控件"""
    for c in widget.winfo_children():
        if isinstance(c, classes) and (text is None or text in (c.cget('text') or '')):
            return c
        hit = find(c, classes, text)
        if hit:
            return hit
    return None


def main():
    results = {}

    # ---- 1) 设置窗口：三个新控件 ----
    root = tkinterdnd2.TkinterDnD.Tk()
    root.withdraw()

    def check_settings():
        tops = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        if not tops:
            results['设置窗口打开'] = False
            root.destroy()
            return
        sw = tops[-1]
        results['设置窗口打开'] = True
        for label in ('最大边长', '目标体积', '压缩输出子文件夹'):
            results[f'控件「{label}」'] = find(sw, (tk.Label, ttk.Label), label) is not None
        sw.destroy()
        root.destroy()

    root.after(1200, check_settings)
    pw.open_settings_window(parent=root)

    # ---- 2) 主选择窗口：无水印勾选框 ----
    captured = []
    orig_tk = tkinterdnd2.TkinterDnD.Tk

    class Rec(orig_tk):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            captured.append(self)
            self.after(2500, self._check)

        def _check(self):
            cb_classes = (tk.Checkbutton, ttk.Checkbutton)
            results['主窗口「无水印(仅压缩)」勾选框'] = find(self, cb_classes, '无水印') is not None
            self.destroy()

    tkinterdnd2.TkinterDnD.Tk = Rec
    try:
        pw.select_paths_gui()
    finally:
        tkinterdnd2.TkinterDnD.Tk = orig_tk

    ok = all(results.values())
    for k, v in results.items():
        print(f'SMOKE {k}:', 'OK' if v else '缺失')
    print('SMOKE 结果:', 'PASS' if ok else 'FAIL')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
