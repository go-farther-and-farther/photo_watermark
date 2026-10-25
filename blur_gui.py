# -*- coding: utf-8 -*-
"""清糊片窗口：连拍分组 → 组内相对锐度 → 缩略图逐帧确认 → 勾选的移入回收站

作为水印工具（photo_watermark）的子窗口打开；独立运行走 main()。
逻辑全在 blur_cull.py，这里只做界面与线程调度。

相比首版的改进（2026-10-26）：
- 扫描解码时显示进度条；
- 判定结果出来后，糊/软帧在窗口右侧列成缩略图行（后台线程生成），
  删除前逐帧勾选——软帧默认不勾，想删可以勾上；
- 「移入回收站」只删勾选的帧。
"""
import os
import queue
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import blur_cull as bc

# 与主界面同一套深蓝配色
HEAD_BG = '#1e3a5f'
BTN_BG = '#3d6ea8'
BTN_FG = '#ffffff'
TEXT_BG = '#ffffff'

THUMB_TILE = 150          # 缩略图宽（像素），行高按 3:2 比例约 100
REVIEW_W = 360            # 右侧确认栏宽


class App:
    def __init__(self, win):
        self.win = win
        self.win.title('清糊片 · 连拍选帧')
        self.win.geometry('1020x680')
        self.win.minsize(860, 540)

        self.frames = []
        self.groups = []
        self.out_dir = None
        self.q = queue.Queue()
        self.busy = False
        self._rows = []            # 确认栏行：[{'frame', 'var', 'thumb'}]
        self._thumbs = {}          # name -> PIL.Image（主线程转 PhotoImage 用）

        self.folder = tk.StringVar()
        self.ratio = tk.StringVar(value=f'{bc.DEFAULT_RATIO:.2f}')
        self.gap = tk.StringVar(value=f'{bc.DEFAULT_GAP:g}')
        self.after = tk.StringVar()
        self.del_sidecar = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value='选一个文件夹，先扫描出报告 —— 扫描不会删任何文件')

        self._build()
        self.win.after(120, self._poll)

    # ---------------- 界面 ----------------
    def _build(self):
        head = tk.Frame(self.win, bg=HEAD_BG)
        head.pack(fill='x')
        tk.Label(head, text='🧹 清糊片', font=('Microsoft YaHei UI', 15, 'bold'),
                 bg=HEAD_BG, fg='#ffffff').pack(side='left', padx=14, pady=10)
        tk.Label(head, text='同一场景连拍里挑出最锐的，糊的进回收站',
                 font=('Microsoft YaHei UI', 9), bg=HEAD_BG, fg='#9db4d0').pack(side='left')

        body = tk.Frame(self.win, padx=14, pady=10)
        body.pack(fill='both', expand=True)

        row = tk.Frame(body)
        row.pack(fill='x')
        tk.Label(row, text='文件夹', width=7, anchor='w').pack(side='left')
        tk.Entry(row, textvariable=self.folder).pack(side='left', fill='x', expand=True, padx=(0, 6))
        tk.Button(row, text='浏览…', command=self._browse, width=8).pack(side='left')

        opt = tk.Frame(body)
        opt.pack(fill='x', pady=(8, 0))
        tk.Label(opt, text='删除线', anchor='w').pack(side='left')
        tk.Spinbox(opt, from_=0.50, to=0.95, increment=0.05, width=5,
                   textvariable=self.ratio).pack(side='left', padx=(4, 2))
        tk.Label(opt, text='（相对本组最锐帧，低于它就删）').pack(side='left')
        tk.Label(opt, text='   组间隔').pack(side='left')
        tk.Spinbox(opt, from_=2, to=600, increment=5, width=5,
                   textvariable=self.gap).pack(side='left', padx=(4, 2))
        tk.Label(opt, text='秒').pack(side='left')

        opt2 = tk.Frame(body)
        opt2.pack(fill='x', pady=(6, 0))
        tk.Label(opt2, text='只看这张之后', anchor='w').pack(side='left')
        tk.Entry(opt2, textvariable=self.after, width=22).pack(side='left', padx=(4, 4))
        tk.Label(opt2, text='（文件名，如 P1093278.RW2；留空=整个文件夹）').pack(side='left')
        tk.Checkbutton(opt2, text='同名 JPG 一起删', variable=self.del_sidecar).pack(side='left',
                                                                                     padx=(12, 0))

        btns = tk.Frame(body)
        btns.pack(fill='x', pady=(10, 8))
        self.btn_scan = tk.Button(btns, text='① 扫描出报告', command=self._scan,
                                  bg=BTN_BG, fg=BTN_FG, relief='flat', padx=14, pady=4,
                                  cursor='hand2')
        self.btn_scan.pack(side='left')
        self.btn_open = tk.Button(btns, text='打开报告文件夹', command=self._open_out,
                                  state='disabled', padx=12, pady=4)
        self.btn_open.pack(side='left', padx=(8, 0))
        self.btn_del = tk.Button(btns, text='③ 移入回收站', command=self._delete,
                                 state='disabled', bg='#a33', fg='#fff', relief='flat',
                                 padx=14, pady=4, cursor='hand2')
        self.btn_del.pack(side='left', padx=(8, 0))

        # 主区：左日志 + 右待删确认（缩略图逐帧勾选）
        main = tk.Frame(body)
        main.pack(fill='both', expand=True)
        self.log = tk.Text(main, height=18, wrap='none', bg=TEXT_BG, relief='flat',
                           font=('Consolas', 9))
        ys = ttk.Scrollbar(main, orient='vertical', command=self.log.yview)
        self.log.configure(yscrollcommand=ys.set)
        self.log.pack(side='left', fill='both', expand=True)
        ys.pack(side='left', fill='y')

        right = tk.Frame(main)
        right.pack(side='left', fill='both', expand=False, padx=(10, 0))
        self.review_title = tk.Label(right, text='待删确认（出报告后列在这里）',
                                     anchor='w', fg='#555')
        self.review_title.pack(fill='x')
        self.canvas = tk.Canvas(right, bg='#f6f8fb', highlightthickness=0, width=REVIEW_W)
        self.rscroll = ttk.Scrollbar(right, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.rscroll.set)
        self.canvas.pack(side='left', fill='both', expand=True, pady=(4, 0))
        self.rscroll.pack(side='left', fill='y')
        self.inner = tk.Frame(self.canvas, bg='#f6f8fb')
        self._inner_id = self.canvas.create_window((0, 0), window=self.inner,
                                                   anchor='nw')
        self.inner.bind('<Configure>', lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(
            self._inner_id, width=e.width))
        # 鼠标滚轮滚动确认栏
        self.canvas.bind('<Enter>', lambda e: self.canvas.bind_all(
            '<MouseWheel>', lambda ev: self.canvas.yview_scroll(
                -1 if ev.delta > 0 else 1, 'units')))
        self.canvas.bind('<Leave>', lambda e: self.canvas.unbind_all('<MouseWheel>'))

        bar = tk.Frame(self.win, bg='#eef2f7')
        bar.pack(fill='x')
        tk.Label(bar, textvariable=self.status, anchor='w', bg='#eef2f7',
                 fg='#334').pack(side='left', padx=12, pady=5)
        self.pbar = ttk.Progressbar(bar, length=220, mode='determinate')
        self.pbar.pack(side='right', padx=12, pady=4)

    # ---------------- 交互 ----------------
    def _browse(self):
        d = filedialog.askdirectory(title='选择要清理的照片文件夹')
        if d:
            self.folder.set(d)

    def _write(self, text):
        self.log.insert('end', text + '\n')
        self.log.see('end')

    def _clear(self):
        self.log.delete('1.0', 'end')

    def _scan(self):
        if self.busy:
            return
        folder = Path(self.folder.get().strip())
        if not folder.is_dir():
            messagebox.showwarning('文件夹不存在', '请先选择一个存在的照片文件夹。')
            return
        try:
            ratio = float(self.ratio.get())
            gap = float(self.gap.get())
        except ValueError:
            messagebox.showwarning('参数不对', '删除线和组间隔都要是数字。')
            return
        if not 0.3 <= ratio <= 1.0:
            messagebox.showwarning('删除线', '删除线请填 0.30 ~ 1.00 之间的数。')
            return
        # 修首版遗漏：范围校验弹完警告必须回来，不能带着越界值开扫
        if not 1 <= gap <= 3600:
            messagebox.showwarning('组间隔', '组间隔请填 1 ~ 3600 之间的秒数。')
            return

        self.busy = True
        self.btn_scan.config(state='disabled', text='扫描中…')
        self.btn_del.config(state='disabled')
        self._clear()
        self._clear_review()
        self.pbar.config(value=0, maximum=100)
        self._write(f'扫描 {folder}')
        self._write('（逐帧解码 RAW，几十张要一两分钟；右侧出报告后可逐帧确认）')
        threading.Thread(target=self._worker, args=(folder, ratio, gap,
                                                   self.after.get().strip()), daemon=True).start()

    def _worker(self, folder, ratio, gap, after):
        try:
            raws = bc.filter_files(bc.scan_folder(folder), after=after or None)
            if not raws:
                self.q.put(('error', '这个文件夹里没有 RAW（可用"只看这张之后"缩小范围）'))
                return
            frames = [bc.Frame(path=p) for p in raws]
            bc.score_frames(frames, verbose=False,
                            on_progress=lambda i, t, f: self.q.put(('progress', i, t, f.name)))
            groups = bc.group_by_time(frames, gap=gap)
            bc.assign_relative(groups)
            bc.decide(groups, ratio=ratio)
            out_dir = bc.default_out_dir(folder)
            bc.write_csv(frames, out_dir / '糊片报告.csv')
            bc.write_contact_sheet(frames, out_dir / '糊片报告_联系表.jpg')
            self.q.put(('done', frames, groups, out_dir))
        except Exception:                                  # noqa: BLE001
            self.q.put(('error', traceback.format_exc(limit=3)))

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == 'progress':
                    _, i, total, name = msg
                    self.pbar.config(maximum=total, value=i)
                    self.status.set(f'解码 {i}/{total}：{name}')
                elif kind == 'done':
                    self._show_result(*msg[1:])
                elif kind == 'thumb':                      # 后台缩略图：主线程转 PhotoImage
                    _, name, pil_img = msg
                    self._set_thumb(name, pil_img)
                elif kind == 'error':
                    self._finish()
                    self._write('出错了：\n' + msg[1])
                    messagebox.showerror('扫描失败', msg[1][:500])
        except queue.Empty:
            pass
        self.win.after(120, self._poll)

    def _finish(self):
        self.busy = False
        self.btn_scan.config(state='normal', text='① 扫描出报告')

    def _show_result(self, frames, groups, out_dir):
        self.frames, self.groups, self.out_dir = frames, groups, out_dir
        self._finish()
        self.pbar.config(value=0)
        self._clear()
        self._write(f'{len(frames)} 张 RAW，分成 {len(groups)} 组（组内间隔 ≤ '
                    f'{float(self.gap.get()):g}s）')
        for gi, g in enumerate(groups, 1):
            best = max(g, key=lambda f: f.score)
            self._write(f'  组{gi}: {g[0].name} ~ {g[-1].name}  {len(g)} 帧  '
                        f'最佳 {best.name}')
            notes = bc.exposure_conflicts(g)
            if notes:
                self._write(f'        提示：组内曝光不一致（{"，".join(notes)}），'
                            f'结论请结合联系表看')
        blur = [f for f in frames if f.decision == bc.DEC_BLUR]
        soft = [f for f in frames if f.decision == bc.DEC_SOFT]
        self._write('')
        self._write(f'判定：糊 {len(blur)} 张 / 软 {len(soft)} 张（默认不删）/ '
                    f'保留 {len(frames) - len(blur) - len(soft)} 张')
        self._write('')
        self._write(f'报告：{out_dir / "糊片报告.csv"}')
        self._write(f'联系表：{out_dir / "糊片报告_联系表.jpg"}')
        self.btn_open.config(state='normal')

        self._build_review(blur, soft)
        if blur:
            self._write('右边已列出糊/软帧：确认勾选后按「移入回收站」，软帧想删自己勾上。')
            self.btn_del.config(state='normal')
            self.status.set(f'糊 {len(blur)} 张已勾选待确认；右侧缩略图可逐帧取舍')
        else:
            self.btn_del.config(state='disabled')
            self.status.set('没有判为糊的帧，不用删')

    # ---------------- 待删确认栏 ----------------
    def _clear_review(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self._rows.clear()
        self._thumbs.clear()
        self.review_title.config(text='待删确认（出报告后列在这里）')

    def _build_review(self, blur, soft):
        self._clear_review()
        rows = [(f, True) for f in blur] + [(f, False) for f in soft]
        if not rows:
            self.review_title.config(text='待删确认（没有糊/软帧）')
            self._sync_del_btn()
            return
        self.review_title.config(text=f'待删确认：糊 {len(blur)}（已勾）+ 软 {len(soft)}（未勾）'
                                       '——勾谁删谁')
        for f, checked in rows:
            var = tk.BooleanVar(value=checked)
            r = tk.Frame(self.inner, bg='#ffffff', bd=1, relief='solid')
            r.pack(fill='x', pady=3, padx=4)
            tk.Checkbutton(r, variable=var, bg='#ffffff').pack(side='left',
                                                               padx=(4, 0), pady=2)
            thumb = tk.Label(r, text='…', bg='#dddddd', width=20, height=6)
            thumb.pack(side='left', padx=6, pady=4)
            info = tk.Frame(r, bg='#ffffff')
            info.pack(side='left', fill='x', expand=True, pady=4)
            tag = '糊' if f.decision == bc.DEC_BLUR else '软'
            color = '#c22' if tag == '糊' else '#b70'
            tk.Label(info, text=f'{tag} {f.rel * 100:4.1f}%', fg=color, bg='#ffffff',
                     font=('Microsoft YaHei UI', 10, 'bold'), anchor='w').pack(fill='x')
            tk.Label(info, text=f.name, bg='#ffffff', anchor='w',
                     font=('Consolas', 8)).pack(fill='x')
            tk.Label(info, text=f'{f.shutter}  ISO {f.iso}', bg='#ffffff', fg='#666',
                     anchor='w', font=('Microsoft YaHei UI', 8)).pack(fill='x')
            var.trace_add('write', lambda *_: self._sync_del_btn())
            self._rows.append({'frame': f, 'var': var, 'thumb': thumb, 'row': r})
        self._sync_del_btn()
        # 缩略图后台生成（糊+软才有，通常不多；RAW 解码不能卡界面）
        threading.Thread(target=self._make_thumbs, args=([f for f, _ in rows],),
                         daemon=True).start()

    def _make_thumbs(self, frames):
        from PIL import Image
        for f in frames:
            try:
                t = bc._thumb(f.path, THUMB_TILE)          # BGR ndarray（有侧车则快）
                img = Image.fromarray(t[..., ::-1].copy())  # BGR->RGB
                img.thumbnail((THUMB_TILE, THUMB_TILE))
                self.q.put(('thumb', f.name, img))
            except Exception:                              # noqa: BLE001
                pass                                       # 缩略图失败不影响功能

    def _set_thumb(self, name, pil_img):
        from PIL import ImageTk
        for row in self._rows:
            if row['frame'].name == name:
                row['thumb'].configure(width=0, height=0, image='',
                                       text='')
                row['photo'] = ImageTk.PhotoImage(pil_img)
                row['thumb'].configure(image=row['photo'])
                return

    def _checked_frames(self):
        return [r['frame'] for r in self._rows if r['var'].get()]

    def _sync_del_btn(self):
        n = len(self._checked_frames())
        self.btn_del.config(text=f'③ 移入回收站（勾选 {n} 张）',
                            state='normal' if n else 'disabled')

    def _open_out(self):
        if not self.out_dir:
            return
        target = self.out_dir / '糊片报告_联系表.jpg'
        if target.exists():
            os.startfile(str(target))                      # noqa: S606
        else:
            os.startfile(str(self.out_dir))                # noqa: S606

    def _delete(self):
        picked = self._checked_frames()
        if not picked:
            return
        targets = bc.collect_deletions(picked, with_sidecar=self.del_sidecar.get())
        size = sum(p.stat().st_size for p in targets if p.exists()) / 1024 / 1024
        msg = (f'把勾选的 {len(picked)} 张移入回收站？\n\n'
               f'连同同名 JPG 共 {len(targets)} 个文件，约 {size:.0f}MB。\n'
               f'回收站里可以恢复。')
        if not messagebox.askyesno('确认删除', msg):
            return
        done, failed = bc.delete_files(targets, recycle=True)
        self._write('')
        self._write(f'已移入回收站 {len(done)} 个文件')
        if failed:
            self._write(f'失败 {len(failed)} 个：')
            for p in failed:
                self._write(f'  {p.name}')
        # 从确认栏撤掉已删的行
        for row in self._rows[:]:
            if not row['frame'].path.exists():
                row['row'].destroy()
                self._rows.remove(row)
        self._sync_del_btn()
        self.status.set(f'已删 {len(done)} 个；想再筛一遍请重新扫描')


def open_blur_window(parent):
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
