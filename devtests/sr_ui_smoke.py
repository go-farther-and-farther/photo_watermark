# -*- coding: utf-8 -*-
"""主窗口集成冒烟测试：自动点击标题栏「🔬 超分」按钮，验证超分窗口弹出。

用法: python devtests/sr_ui_smoke.py
（需要 rawpy/opencv/tifffile/piexif 已安装，否则按钮只会弹依赖缺失提示）
"""
import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinterdnd2  # noqa: E402

SR_TITLE = '手持多帧超分辨率合成（连拍分组版）'


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
            sr_btn = find(self, btn_classes, '超分')
            print('SMOKE 标题栏超分按钮:', sr_btn is not None)
            if sr_btn:
                sr_btn.invoke()

                def check2():
                    tops = [w for w in self.winfo_children() if isinstance(w, tk.Toplevel)]
                    print('SMOKE 弹出的窗口:', [w.title() for w in tops])
                    print('SMOKE 超分窗口出现:', any(w.title() == SR_TITLE for w in tops))
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
