# -*- coding: utf-8 -*-
"""主窗口冒烟：真实构建 UI（不进入事件循环），检查新增的「调色模板」面板不报错"""
import sys
from pathlib import Path

sys.path.insert(0, r'D:\Software files\photo_watermark')

import tkinter as tk

_orig = tk.Tk.mainloop
tk.Tk.mainloop = lambda self, n=0: self.destroy()   # 构建完立刻销毁，不弹窗停留

import photo_watermark as pw  # noqa: E402

print('模板列表:', pw.color_grade.template_names(pw.GRADE_TEMPLATES))
print('当前模板:', pw.GRADE_TEMPLATE, '强度:', pw.GRADE_STRENGTH)
paths, style, brand = pw.select_paths_gui()
print('OK 窗口构建成功, 返回:', paths, style, brand)
