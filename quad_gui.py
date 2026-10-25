# -*- coding: utf-8 -*-
"""四格对比窗口：选参考图与对比面板 → 在图上点裁切点 → 生成同色调对比网格

作为水印工具（photo_watermark）的子窗口打开；独立运行走 main()。
逻辑全在 quad_compare.py，这里只做界面与线程调度。
"""
import contextlib
import os
import queue
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import quad_compare as qc

# 与主界面同一套深蓝配色
HEAD_BG = '#1e3a5f'
BTN_BG = '#3d6ea8'
BTN_FG = '#ffffff'
TEXT_BG = '#ffffff'

IMG_TYPES = [('图片', '*.jpg *.jpeg *.png *.tif *.tiff *.bmp'), ('所有文件', '*.*')]
PREVIEW_W = 620          # 预览画布宽（图按宽缩放到这个值）
PANEL_DEFAULTS = ('8 帧合成', '最锐单帧', '中位帧')


class App:
    def __init__(self, win):
        self.win = win
        self.win.title('四格对比 · 同裁切同色调')
        self.win.geometry('1020x680')
        self.win.minsize(880, 560)

        self.q = queue.Queue()
        self.busy = False
        self.out_dir = None
        self.spots = []            # [(点名, fx, fy)]
        self._photo = None         # 预览 PhotoImage 引用（防被回收）
        self._disp = None          # (显示宽, 显示高, 偏移x, 偏移y)

        self.ref = tk.StringVar()
        self.ref_label = tk.StringVar(value='N 帧合成')
        self.labels = [tk.StringVar(value=v) for v in PANEL_DEFAULTS]
        self.panels = [tk.StringVar() for _ in PANEL_DEFAULTS]
        self.spot_name = tk.StringVar()
        self.crop = tk.StringVar(value=f'{qc.DEFAULT_CROP[0]}x{qc.DEFAULT_CROP[1]}')
        self.prefix = tk.StringVar(value=qc.DEFAULT_PREFIX)
        self.title_fmt = tk.StringVar(value=qc.DEFAULT_TITLE)
        self.out = tk.StringVar()
        self._out_auto = False     # 输出目录是不是自动填的（自动填的才允许被覆盖）
        self.status = tk.StringVar(value='选参考图（N 帧合成成品）→ 在图上点几个裁切点 → 生成')

        self._build()
        self.win.after(120, self._poll)

    # ---------------- 界面 ----------------
    def _build(self):
        head = tk.Frame(self.win, bg=HEAD_BG)
        head.pack(fill='x')
        tk.Label(head, text='📐 四格对比', font=('Microsoft YaHei UI', 15, 'bold'),
                 bg=HEAD_BG, fg='#ffffff').pack(side='left', padx=14, pady=10)
        tk.Label(head, text='同一裁切、同一色调：多帧合成 vs 少帧 vs 单帧，差别只剩噪声',
                 font=('Microsoft YaHei UI', 9), bg=HEAD_BG, fg='#9db4d0').pack(side='left')

        body = tk.Frame(self.win, padx=12, pady=8)
        body.pack(fill='both', expand=True)

        row = tk.Frame(body)
        row.pack(fill='x')
        tk.Label(row, text='参考图', width=8, anchor='w').pack(side='left')
        tk.Entry(row, textvariable=self.ref).pack(side='left', fill='x', expand=True,
                                                  padx=(0, 6))
        tk.Button(row, text='浏览…', width=8, command=self._browse_ref).pack(side='left')
        tk.Button(row, text='载入预览', width=9, command=self._load_preview).pack(side='left',
                                                                                 padx=(6, 0))
        tk.Label(row, text=' 参考格标签', fg='#555').pack(side='left', padx=(10, 0))
        tk.Entry(row, textvariable=self.ref_label, width=12).pack(side='left')

        for i in range(len(PANEL_DEFAULTS)):
            r = tk.Frame(body)
            r.pack(fill='x', pady=(5, 0))
            tk.Label(r, text=f'面板 {i + 1}', width=8, anchor='w').pack(side='left')
            tk.Entry(r, textvariable=self.labels[i], width=12).pack(side='left',
                                                                    padx=(0, 6))
            tk.Entry(r, textvariable=self.panels[i]).pack(side='left', fill='x',
                                                          expand=True, padx=(0, 6))
            tk.Button(r, text='浏览…', width=8,
                      command=lambda i=i: self._browse_panel(i)).pack(side='left')

        opt = tk.Frame(body)
        opt.pack(fill='x', pady=(8, 0))
        tk.Label(opt, text='裁切尺寸', anchor='w').pack(side='left')
        tk.Entry(opt, textvariable=self.crop, width=9).pack(side='left', padx=(4, 10))
        tk.Label(opt, text='文件名前缀').pack(side='left')
        tk.Entry(opt, textvariable=self.prefix, width=12).pack(side='left', padx=(4, 10))
        tk.Label(opt, text='标题（{label}=点名）').pack(side='left')
        tk.Entry(opt, textvariable=self.title_fmt).pack(side='left', fill='x',
                                                        expand=True, padx=(4, 0))

        orow = tk.Frame(body)
        orow.pack(fill='x', pady=(6, 0))
        tk.Label(orow, text='输出到', width=8, anchor='w').pack(side='left')
        tk.Entry(orow, textvariable=self.out).pack(side='left', fill='x', expand=True,
                                                   padx=(0, 6))
        tk.Button(orow, text='浏览…', width=8, command=self._browse_out).pack(side='left')

        mid = tk.Frame(body)
        mid.pack(fill='both', expand=True, pady=(10, 0))
        # 左：预览画布（点击加裁切点）
        left = tk.Frame(mid)
        left.pack(side='left', fill='both', expand=True)
        tip = tk.Frame(left)
        tip.pack(fill='x')
        tk.Label(tip, text='点名', fg='#555').pack(side='left')
        tk.Entry(tip, textvariable=self.spot_name, width=14).pack(side='left', padx=(4, 8))
        tk.Label(tip, text='← 可留空；在下图点一下就加一个裁切点', fg='#777').pack(side='left')
        self.canvas = tk.Canvas(left, bg='#101014', height=430, cursor='crosshair',
                                highlightthickness=0)
        self.canvas.pack(fill='both', expand=True, pady=(6, 0))
        self.canvas.bind('<Button-1>', self._on_click)
        self._draw_placeholder('载入参考图后这里显示预览；点图加裁切点')
        # 右：点列表 + 日志
        right = tk.Frame(mid)
        right.pack(side='left', fill='y', padx=(10, 0))
        self.spot_list = tk.Listbox(right, width=30, height=8, exportselection=False)
        self.spot_list.pack(fill='x')
        tk.Button(right, text='删除选中点', command=self._del_spot).pack(anchor='w',
                                                                        pady=(4, 0))
        self.log = tk.Text(right, width=30, height=18, wrap='char', bg=TEXT_BG,
                           relief='flat', font=('Consolas', 9))
        self.log.pack(fill='both', expand=True, pady=(6, 0))

        btns = tk.Frame(body)
        btns.pack(fill='x', pady=(10, 6))
        self.btn_go = tk.Button(btns, text='生成对比图', command=self._run,
                                bg=BTN_BG, fg=BTN_FG, relief='flat', padx=16, pady=4,
                                cursor='hand2')
        self.btn_go.pack(side='left')
        self.btn_open = tk.Button(btns, text='打开输出文件夹', command=self._open_out,
                                  state='disabled', padx=12, pady=4)
        self.btn_open.pack(side='left', padx=(8, 0))

        bar = tk.Frame(self.win, bg='#eef2f7')
        bar.pack(fill='x')
        tk.Label(bar, textvariable=self.status, anchor='w', bg='#eef2f7',
                 fg='#334').pack(side='left', padx=12, pady=5)

    # ---------------- 预览与裁切点 ----------------
    def _browse_ref(self):
        p = filedialog.askopenfilename(title='选参考图（N 帧合成成品）',
                                       filetypes=IMG_TYPES)
        if not p:
            return
        self.ref.set(p)
        self._auto_out()
        self._load_preview()

    def _browse_panel(self, i):
        p = filedialog.askopenfilename(title=f'选面板 {i + 1} 的图', filetypes=IMG_TYPES)
        if p:
            self.panels[i].set(p)

    def _browse_out(self):
        d = filedialog.askdirectory(title='选输出目录')
        if d:
            self.out.set(d)
            self._out_auto = False

    def _auto_out(self):
        """默认输出：Pictures\\JPG\\SR对比图\\<参考图父目录名>（对比图不进 SR输出，LR 会整目录扫）"""
        ref = Path(self.ref.get())
        pics = Path.home() / 'Pictures'
        base = pics / 'JPG' / 'SR对比图' / ref.parent.name if pics.is_dir() \
            else ref.parent / 'SR对比图'
        if self._out_auto or not self.out.get().strip():
            self.out.set(str(base))
            self._out_auto = True

    def _load_preview(self):
        p = self.ref.get().strip()
        if not p:
            return
        if not Path(p).is_file():
            messagebox.showwarning('参考图不存在', f'找不到：\n{p}')
            return
        try:
            img = qc.imread_color(p)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror('读图失败', str(e))
            return
        self._show_preview(img)
        self.status.set(f'参考图 {img.shape[1]}x{img.shape[0]}；在图上点裁切点，'
                        f'右侧可删；点够了按「生成对比图」')

    def _show_preview(self, img):
        from PIL import Image, ImageTk
        h, w = img.shape[:2]
        dw = min(PREVIEW_W, w)
        dh = max(1, int(h * dw / w))
        pil = Image.fromarray(qc.cv2.cvtColor(img, qc.cv2.COLOR_BGR2RGB)).resize((dw, dh))
        self._photo = ImageTk.PhotoImage(pil)
        self.canvas.delete('all')
        cw = max(int(self.canvas.winfo_width() or PREVIEW_W), dw)
        ox, oy = (cw - dw) // 2, 0
        self._disp = (dw, dh, ox, oy)
        self.canvas.create_image(ox, oy, image=self._photo, anchor='nw')
        self.canvas.configure(scrollregion=(0, 0, cw, dh))
        for i, (_, fx, fy) in enumerate(self.spots, 1):
            self._draw_marker(i, fx, fy)

    def _draw_placeholder(self, text):
        self._disp = None
        self.canvas.delete('all')
        self.canvas.create_text(PREVIEW_W // 2, 215, text=text, fill='#8899aa',
                                font=('Microsoft YaHei UI', 10))

    def _draw_marker(self, idx, fx, fy):
        dw, dh, ox, oy = self._disp
        x, y = ox + fx * dw, oy + fy * dh
        c = '#ff5544'
        self.canvas.create_line(x - 10, y, x + 10, y, fill=c, width=2, tags='mark')
        self.canvas.create_line(x, y - 10, x, y + 10, fill=c, width=2, tags='mark')
        self.canvas.create_text(x + 12, y - 10, text=str(idx), fill=c, anchor='w',
                                font=('Microsoft YaHei UI', 10, 'bold'), tags='mark')

    def _on_click(self, ev):
        if not self._disp or self.busy:
            return
        dw, dh, ox, oy = self._disp
        if not (ox <= ev.x < ox + dw and oy <= ev.y < oy + dh):
            return
        fx, fy = (ev.x - ox) / dw, (ev.y - oy) / dh
        name = self.spot_name.get().strip() or f'位置{len(self.spots) + 1}'
        self.spots.append((name, fx, fy))
        self.spot_name.set('')
        self._refresh_spot_list()
        self._draw_marker(len(self.spots), fx, fy)
        self.status.set(f'已加 {len(self.spots)} 个裁切点（{name}）')

    def _refresh_spot_list(self):
        self.spot_list.delete(0, 'end')
        for name, fx, fy in self.spots:
            self.spot_list.insert('end', f'{name}  ({fx:.3f}, {fy:.3f})')

    def _del_spot(self):
        sel = self.spot_list.curselection()
        if not sel:
            return
        del self.spots[sel[0]]
        self._refresh_spot_list()
        if self._disp:
            self._redraw_markers()

    def _redraw_markers(self):
        self.canvas.delete('mark')
        for i, (_, fx, fy) in enumerate(self.spots, 1):
            self._draw_marker(i, fx, fy)

    # ---------------- 生成 ----------------
    def _collect(self):
        """校验并收集参数，不合法就弹窗并返回 None"""
        ref_p = Path(self.ref.get().strip())
        if not ref_p.is_file():
            messagebox.showwarning('参考图', '先选一张存在的参考图（N 帧合成成品）。')
            return None
        panels = []
        for i in range(len(PANEL_DEFAULTS)):
            p = self.panels[i].get().strip()
            if not p:
                continue
            if not Path(p).is_file():
                messagebox.showwarning('面板图不存在', f'面板 {i + 1}：\n{p}')
                return None
            panels.append((self.labels[i].get().strip() or Path(p).stem, p))
        if not panels:
            messagebox.showwarning('面板', '至少填一个对比面板（其余留空跳过）。')
            return None
        if not self.spots:
            messagebox.showwarning('裁切点', '在预览图上点至少一个裁切点。')
            return None
        try:
            cw, ch = (int(v) for v in self.crop.get().lower().split('x'))
            if cw < 50 or ch < 50:
                raise ValueError
        except ValueError:
            messagebox.showwarning('裁切尺寸', '裁切尺寸要写成 宽x高（都 ≥50），如 1000x680。')
            return None
        out = self.out.get().strip()
        if not out:
            messagebox.showwarning('输出目录', '填一个输出目录。')
            return None
        return dict(ref=ref_p, panels=panels, crop=(cw, ch), out=out)

    def _run(self):
        if self.busy:
            return
        cfg = self._collect()
        if cfg is None:
            return
        self.busy = True
        self.btn_go.config(state='disabled', text='生成中…')
        self.log.delete('1.0', 'end')
        args = (cfg['ref'], cfg['panels'], list(self.spots), cfg['out'], cfg['crop'])
        threading.Thread(target=self._worker, args=args, daemon=True).start()

    def _worker(self, ref, panels, spots, out, crop):
        class _QWriter:                                    # process 输出直接进日志队列
            def write(_, s):
                if s.strip():
                    self.q.put(('log', s.rstrip()))
            def flush(_):
                pass
        try:
            with contextlib.redirect_stdout(_QWriter()):
                outs = qc.generate_comparisons(
                    ref, panels, spots, Path(out), crop_wh=crop,
                    title_fmt=self.title_fmt.get(), prefix=self.prefix.get(),
                    ref_label=self.ref_label.get().strip() or None,
                    on_progress=lambda i, t, n, p: self.q.put(('progress', i, t, n, p)))
            self.q.put(('done', outs, Path(out)))
        except Exception:                                  # noqa: BLE001
            self.q.put(('error', traceback.format_exc(limit=4)))

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == 'log':
                    self._write(msg[1])
                elif kind == 'progress':
                    _, i, total, name, path = msg
                    self.status.set(f'{i}/{total}：{name}' + (' 完成' if path else ' 失败'))
                elif kind == 'done':
                    outs, out_dir = msg[1], msg[2]
                    self._finish()
                    self.out_dir = out_dir
                    self._write(f'共生成 {len(outs)} 张')
                    for p in outs:
                        self._write(f'  {p.name}')
                    self.btn_open.config(state='normal')
                    self.status.set(f'完成：{len(outs)} 张 → {out_dir}')
                elif kind == 'error':
                    self._finish()
                    self._write('出错了：\n' + msg[1])
                    messagebox.showerror('生成失败', msg[1][:500])
        except queue.Empty:
            pass
        self.win.after(120, self._poll)

    def _write(self, text):
        self.log.insert('end', text + '\n')
        self.log.see('end')

    def _finish(self):
        self.busy = False
        self.btn_go.config(state='normal', text='生成对比图')

    def _open_out(self):
        if self.out_dir and self.out_dir.is_dir():
            os.startfile(str(self.out_dir))               # noqa: S606


def open_quad_window(parent):
    """作为水印工具（photo_watermark）的子窗口打开；独立运行走 main()"""
    win = tk.Toplevel(parent)
    win.transient(parent)
    win.withdraw()
    App(win)
    win.deiconify()
    return win


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
