# -*- coding: utf-8 -*-
"""主窗口集成冒烟测试：自动点击标题栏「📷 EXIF」按钮，验证 EXIF 窗口弹出。

用法: python devtests/main_window_smoke.py
"""
import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinterdnd2  # noqa: E402


def main():
    # 记录 select_paths_gui 创建的 Tk 根窗口，并在 mainloop 启动后自动检查
    orig_tk = tkinterdnd2.TkinterDnD.Tk
    captured = []

    class Rec(orig_tk):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            captured.append(self)
            self.after(3000, self._check)

        def _check(self):
            from tkinter import ttk

            def find(w, classes, text=None):
                for c in w.winfo_children():
                    if isinstance(c, classes) and (text is None or text in (c.cget('text') or '')):
                        return c
                    hit = find(c, classes, text)
                    if hit:
                        return hit
                return None

            btn_classes = (tk.Button, ttk.Button)
            exif_btn = find(self, btn_classes, 'EXIF')
            print('SMOKE 标题栏EXIF按钮:', exif_btn is not None)
            viewer_btn = find(self, btn_classes, '查看EXIF')
            print('SMOKE 查看EXIF按钮:', viewer_btn is not None)
            if exif_btn:
                exif_btn.invoke()

                def check2():
                    tops = [w for w in self.winfo_children() if isinstance(w, tk.Toplevel)]
                    print('SMOKE 弹出的窗口:', [w.title() for w in tops])
                    self.destroy()

                self.after(2500, check2)
            else:
                self.destroy()

    tkinterdnd2.TkinterDnD.Tk = Rec

    import photo_watermark as pw
    pw.main()
    print('SMOKE done, main 正常退出')


if __name__ == '__main__':
    main()
