# -*- coding: utf-8 -*-
"""清糊片窗口：连拍分组 → 组内相对锐度 → 确认后把糊的移入回收站

作为水印工具（photo_watermark）的子窗口打开；独立运行走 main()。
逻辑全在 blur_cull.py，这里只做界面与线程调度。
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


class App:
    def __init__(self, win):
        self.win = win
        self.win.title('清糊片 · 连拍选帧')
        self.win.geometry('820x600')
        self.win.minsize(700, 480)

        self.frames = []
        self.groups = []
        self.out_dir = None
        self.q = queue.Queue()
        self.busy = False

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
        tk.Checkbutton(opt2, text='同名 JPG 一起删', variable=self.del_sidecar).pack(side='left', padx=(12, 0))

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

        self.log = tk.Text(body, height=18, wrap='none', bg=TEXT_BG, relief='flat',
                           font=('Consolas', 9))
        ys = ttk.Scrollbar(body, orient='vertical', command=self.log.yview)
        self.log.configure(yscrollcommand=ys.set)
        self.log.pack(side='left', fill='both', expand=True)
        ys.pack(side='right', fill='y')

        bar = tk.Frame(self.win, bg='#eef2f7')
        bar.pack(fill='x')
        tk.Label(bar, textvariable=self.status, anchor='w', bg='#eef2f7',
                 fg='#334').pack(side='left', padx=12, pady=5)

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

        self.busy = True
        self.btn_scan.config(state='disabled', text='扫描中…')
        self.btn_del.config(state='disabled')
        self._clear()
        self._write(f'扫描 {folder}')
        self._write('（逐帧解码 RAW，几十张要一两分钟）')
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
                    self.status.set(f'解码 {i}/{total}：{name}')
                elif kind == 'done':
                    self._show_result(*msg[1:])
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
        self._write(f'判定：糊 {len(blur)} 张 / 软 {len(soft)} 张（不删）/ '
                    f'保留 {len(frames) - len(blur) - len(soft)} 张')
        if blur:
            self._write('待删（相对锐度）：')
            for f in blur:
                self._write(f'  {f.name}  {f.rel * 100:5.1f}%  {f.shutter}')
        if soft:
            self._write('偏软但保留：')
            for f in soft:
                self._write(f'  {f.name}  {f.rel * 100:5.1f}%')
        self._write('')
        self._write(f'报告：{out_dir / "糊片报告.csv"}')
        self._write(f'联系表：{out_dir / "糊片报告_联系表.jpg"}')
        self.btn_open.config(state='normal')
        self.btn_del.config(state='normal' if blur else 'disabled')
        self.status.set(f'糊 {len(blur)} 张待确认；联系表可以直接打开看' if blur
                        else '没有判为糊的帧，不用删')

    def _open_out(self):
        if not self.out_dir:
            return
        target = self.out_dir / '糊片报告_联系表.jpg'
        if target.exists():
            os.startfile(str(target))                      # noqa: S606
        else:
            os.startfile(str(self.out_dir))                # noqa: S606

    def _delete(self):
        blur = [f for f in self.frames if f.decision == bc.DEC_BLUR]
        if not blur:
            return
        targets = bc.collect_deletions(blur, with_sidecar=self.del_sidecar.get())
        size = sum(p.stat().st_size for p in targets if p.exists()) / 1024 / 1024
        msg = (f'把 {len(blur)} 张糊片移入回收站？\n\n'
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
        self.btn_del.config(state='disabled')
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
