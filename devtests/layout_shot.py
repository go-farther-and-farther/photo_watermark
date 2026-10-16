# -*- coding: utf-8 -*-
"""布局验证：全屏尺寸渲染主窗口并抓屏存 PNG（人工查看用，不入 CI）。

用法: python devtests/layout_shot.py [输出.png]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinterdnd2  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent / 'layout_shot.png')

orig_tk = tkinterdnd2.TkinterDnD.Tk
captured = []


class Rec(orig_tk):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        captured.append(self)
        self.after(600, self._go_full)
        self.after(3500, self._shot)

    def _go_full(self):
        if '--no-full' in sys.argv:
            return
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{sw}x{sh}+0+0")

    def _shot(self):
        self.update_idletasks()
        self.attributes('-topmost', True)
        self.lift()
        self.focus_force()
        self.update()
        from PIL import ImageGrab
        # ImageGrab 用物理像素，tkinter 报告逻辑像素（Windows DPI 缩放），需换算
        scale = ImageGrab.grab().width / self.winfo_screenwidth()
        x, y = self.winfo_rootx(), self.winfo_rooty()
        w, h = self.winfo_width(), self.winfo_height()
        img = ImageGrab.grab(bbox=(int(x * scale), int(y * scale),
                                   int((x + w) * scale), int((y + h) * scale)))
        img.save(OUT)
        print(f'SHOT saved: {OUT} ({img.width}x{img.height}) bbox=({x},{y},{w},{h}) scale={scale}')
        self.destroy()


tkinterdnd2.TkinterDnD.Tk = Rec

import photo_watermark as pw  # noqa: E402
# 勾选全部样式 → 全部视图 2 列网格（与用户全屏使用场景一致）
pw.DEFAULT_STYLE = ','.join(k for k, _, _ in pw.STYLE_OPTIONS)
pw.select_paths_gui()
print('done')
