#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手持多帧超分辨率合成 · 连拍分组版（GUI）

选择包含 RAW 连拍的文件夹 -> 自动按拍摄时间分组 -> 每组自动选最锐的帧 -> 合成

- 作为水印工具的子窗口打开（open_sr_window），也可独立运行（python sr_gui.py）
"""
import os
import sys
import configparser

# 必须在导入 numpy 系依赖之前读配置并限制 BLAS/OpenMP 线程（控功耗；8=P核）
_cfg = configparser.ConfigParser()
_exe_dir = os.path.dirname(sys.executable if getattr(sys, 'frozen', False) else __file__)
try:
    _cfg.read(os.path.join(_exe_dir, 'sr_gui.ini'), encoding='utf-8')
except Exception:
    pass
_threads_cfg = _cfg.getint('run', 'threads', fallback=8)
if _threads_cfg > 0:
    for _v in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[_v] = str(_threads_cfg)

import queue
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, ttk, messagebox

sys.path.insert(0, str(Path(__file__).parent))
import burst_tools


class App:
    def __init__(self, root):
        self.root = root
        root.title('手持多帧超分辨率合成（连拍分组版）')
        root.geometry('860x640')
        self.q = queue.Queue()
        self.groups = []
        self._build()
        self.root.after(100, self._poll)

    def _build(self):
        pad = dict(padx=8, pady=4)
        f1 = ttk.Frame(self.root); f1.pack(fill='x', **pad)
        ttk.Label(f1, text='RAW 文件夹:').pack(side='left')
        self.var_src = tk.StringVar(value=str(Path.home() / 'Pictures'))
        ttk.Entry(f1, textvariable=self.var_src).pack(side='left', fill='x', expand=True, padx=6)
        ttk.Button(f1, text='浏览…', command=self._pick_src).pack(side='left')

        f2 = ttk.Frame(self.root); f2.pack(fill='x', **pad)
        ttk.Label(f2, text='输出文件夹:').pack(side='left')
        self.var_dst = tk.StringVar(value='')
        ttk.Entry(f2, textvariable=self.var_dst).pack(side='left', fill='x', expand=True, padx=6)
        ttk.Button(f2, text='浏览…', command=self._pick_dst).pack(side='left')

        f3 = ttk.Frame(self.root); f3.pack(fill='x', **pad)
        self.var_gap = tk.DoubleVar(value=2.0)
        self.var_min = tk.IntVar(value=3)
        self.var_topk = tk.IntVar(value=8)
        self.var_scale = tk.IntVar(value=2)
        self.var_sharpen = tk.DoubleVar(value=0.3)
        self.var_deghost = tk.BooleanVar(value=True)
        self.var_keep_tif = tk.BooleanVar(value=False)
        self.var_threads = tk.IntVar(value=max(_threads_cfg, 0))
        for lab, var, w, tip in [
            ('组内最大间隔(秒)', self.var_gap, 6, '连拍组内相邻两帧的最大时间间隔；大于此间隔算新的一组'),
            ('每组最少帧数', self.var_min, 5, '少于此帧数的组不处理'),
            ('每组最多用帧', self.var_topk, 5, '每组按清晰度选出最锐的前 k 帧'),
            ('放大倍率', self.var_scale, 5, '输出 = 输入分辨率 × 倍率'),
            ('锐化强度', self.var_sharpen, 5, '0-1，0 为不锐化'),
            ('核心数', self.var_threads, 5, '0=全部核心；填 8 只用 P 核，功耗温度大幅下降'),
        ]:
            ttk.Label(f3, text=lab).pack(side='left', padx=(10, 2))
            ttk.Entry(f3, textvariable=var, width=w).pack(side='left')
        ttk.Checkbutton(f3, text='去鬼影', variable=self.var_deghost).pack(side='left', padx=(14, 2))
        self.var_master = tk.BooleanVar(value=True)
        ttk.Checkbutton(f3, text='1.5x调色母版TIF', variable=self.var_master).pack(side='left', padx=6)
        ttk.Checkbutton(f3, text='另存全尺寸TIF(很大)', variable=self.var_keep_tif).pack(side='left', padx=2)

        f4 = ttk.Frame(self.root); f4.pack(fill='x', **pad)
        ttk.Button(f4, text='① 检测连拍分组', command=self._detect).pack(side='left', padx=4)
        ttk.Button(f4, text='② 合成全部组', command=self._run).pack(side='left', padx=4)
        self.var_status = tk.StringVar(value='就绪')
        ttk.Label(f4, textvariable=self.var_status, foreground='#0a7').pack(side='left', padx=16)

        ttk.Label(self.root, text='分组结果:').pack(anchor='w', padx=10)
        self.txt = tk.Text(self.root, height=24)
        self.txt.pack(fill='both', expand=True, padx=10, pady=(0, 10))

    def _pick_src(self):
        p = filedialog.askdirectory(title='选择 RAW 连拍文件夹')
        if p:
            self.var_src.set(p)
            if not self.var_dst.get():
                self.var_dst.set(str(Path(p) / 'SR输出'))

    def _pick_dst(self):
        p = filedialog.askdirectory(title='选择输出文件夹')
        if p:
            self.var_dst.set(p)

    def log(self, s):
        self.txt.insert('end', s + '\n')
        self.txt.see('end')
        self.root.update_idletasks()

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg == '__DONE__':
                    self.var_status.set('完成')
                    self._set_buttons(True)
                else:
                    self.log(msg)
        except queue.Empty:
            pass
        self.root.after(150, self._poll)

    def _set_buttons(self, on):
        for b in self.root.winfo_children():
            if isinstance(b, ttk.Frame):
                for c in b.winfo_children():
                    if isinstance(c, ttk.Button):
                        c['state'] = 'normal' if on else 'disabled'

    def _detect(self):
        src = self.var_src.get().strip()
        if not Path(src).is_dir():
            messagebox.showerror('错误', 'RAW 文件夹不存在')
            return
        self.txt.delete('1.0', 'end')
        self.var_status.set('检测分组中…')
        self._set_buttons(False)

        def work():
            try:
                self.q.put(f'扫描 {src} …')
                groups = burst_tools.scan_folder(src, gap=self.var_gap.get(),
                                                 min_frames=self.var_min.get())
                import datetime
                self.q.put(f'共 {len(groups)} 组连拍，逐组选帧（每组解码约 1-2 秒/帧）…')
                rows = []
                for gi, (t0, frames) in enumerate(groups, 1):
                    kept = burst_tools.select_frames(frames, top_k=self.var_topk.get())
                    tstr = datetime.datetime.fromtimestamp(t0).strftime('%H:%M:%S')
                    self.q.put(f'  组{gi}: {tstr} 起 {len(frames)} 帧 → 选 {len(kept)} 帧')
                    rows.append((gi, tstr, frames, kept))
                self.groups = rows
                self.q.put(f'检测完成，共 {len(rows)} 组。点击「② 合成全部组」开始处理。')
                self.q.put('__DONE__')
            except Exception:
                self.q.put('检测失败:\n' + traceback.format_exc())
                self.q.put('__DONE__')
        threading.Thread(target=work, daemon=True).start()

    def _run(self):
        if not self.groups:
            messagebox.showinfo('提示', '请先「① 检测连拍分组」')
            return
        dst = self.var_dst.get().strip()
        if not dst:
            messagebox.showerror('错误', '请设置输出文件夹')
            return
        Path(dst).mkdir(parents=True, exist_ok=True)
        try:
            _c2 = configparser.ConfigParser()
            _c2['run'] = {'threads': str(self.var_threads.get())}
            with open(os.path.join(_exe_dir, 'sr_gui.ini'), 'w', encoding='utf-8') as f:
                _c2.write(f)
        except Exception:
            pass
        self.var_status.set('合成中…')
        self._set_buttons(False)

        def work():
            try:
                import cv2
                import re
                sys.path.insert(0, str(Path(__file__).parent))
                from sr_process import process
                total = len(self.groups)
                for gi, tstr, frames, kept in self.groups:
                    self.q.put(f'[{gi}/{total}] 组{gi} ({tstr}) {len(frames)}帧选{len(kept)}帧: 合成中…')
                    tmp = Path(dst) / f'.tmp_g{gi}'
                    tmp.mkdir(exist_ok=True)
                    for p, _s in kept:
                        shutil_copy(p, tmp / p.name)
                    base = Path(dst) / f'组{gi:02d}_{tstr.replace(":", "")}'
                    buf = io_log()
                    master = 1.5 if self.var_master.get() else None
                    try:
                        with Redirector(buf):
                            process(str(tmp), str(base) + '.tif', scale=self.var_scale.get(),
                                    use_deghost=self.var_deghost.get(),
                                    sharpen=self.var_sharpen.get(),
                                    threads=self.var_threads.get(),
                                    master_scale=master)
                        m = re.search(r'耗时: ([\d.]+)', buf.getvalue())
                        t = f'{float(m.group(1)):.0f}s' if m else '?'
                    except ValueError as e:
                        self.q.put(f'  [跳过] {e}')
                        clean_tmp(tmp)
                        continue
                    except Exception as e:
                        self.q.put(f'  [失败] {e}')
                        clean_tmp(tmp)
                        continue
                    clean_tmp(tmp)
                    jpg = Path(str(base) + f'_{self.var_scale.get()}x.jpg')
                    if not self.var_keep_tif.get():
                        tif = Path(str(base) + '.tif')
                        if tif.exists():
                            tif.unlink()
                    self.q.put(f'  完成 {t} -> {jpg.name}')
                self.q.put('全部完成！输出在: ' + dst)
                self.q.put('__DONE__')
            except Exception:
                self.q.put('失败:\n' + traceback.format_exc())
                self.q.put('__DONE__')
        threading.Thread(target=work, daemon=True).start()


def shutil_copy(src, dst):
    import shutil
    shutil.copy(str(src), str(dst))


def clean_tmp(tmp):
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)


class Redirector:
    """把 process() 的 print 重定向到 StringIO"""
    def __init__(self, buf):
        self.buf = buf

    def __enter__(self):
        import contextlib
        self.cm = contextlib.redirect_stdout(self.buf)
        return self.cm.__enter__()

    def __exit__(self, *a):
        return self.cm.__exit__(*a)


class io_log:
    def __init__(self):
        import io
        self._b = io.StringIO()

    def getvalue(self):
        return self._b.getvalue()

    def write(self, s):
        return self._b.write(s)

    def flush(self):
        pass


def open_sr_window(parent):
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
