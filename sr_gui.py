#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手持多帧超分辨率合成 · 连拍分组版（GUI）

选择包含 RAW/JPG 连拍的文件夹 -> 自动按拍摄时间分组 -> 逐组判定能否超分 -> 勾选合成

- 作为水印工具的子窗口打开（open_sr_window），也可独立运行（python sr_gui.py）
- 判定与防重复引擎见 sr_suggest.py（设计：docs/superpowers/specs/2026-10-16-sr-suggest-design.md）
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
_k14_cfg = _cfg.get('run', 'k14_backend', fallback='')
if _threads_cfg > 0:
    for _v in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[_v] = str(_threads_cfg)

import datetime
import queue
import shutil
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, ttk, messagebox

sys.path.insert(0, str(Path(__file__).parent))
import sr_suggest
from sr_suggest import scan_and_judge, group_fingerprint, record_done

# 判定 -> (表格显示, 着色 tag)
STATUS_DISPLAY = {
    'ok': ('✅ 可超分', 'ok'),
    'denoise_only': ('🧊 仅降噪', 'denoise'),
    'no': ('❌ 不可超分', 'no'),
    'done': ('✔️ 已完成', 'done'),
}


class App:
    def __init__(self, root):
        self.root = root
        root.title('手持多帧超分辨率合成（连拍分组版）')
        root.geometry('1200x780')
        self.q = queue.Queue()
        self.verdicts = []          # sr_suggest.Verdict 列表（与表格行号一一对应）
        self.checked = set()        # 勾选的组号（1 起）
        self._cancel = False
        self._build()
        self.root.after(100, self._poll)

    def _build(self):
        pad = dict(padx=8, pady=4)
        f1 = ttk.Frame(self.root); f1.pack(fill='x', **pad)
        ttk.Label(f1, text='照片文件夹:').pack(side='left')
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
        self.var_min = tk.IntVar(value=4)
        self.var_topk = tk.IntVar(value=8)
        self.var_scale = tk.IntVar(value=2)
        self.var_sharpen = tk.DoubleVar(value=0.3)
        self.var_deghost = tk.BooleanVar(value=True)
        self.var_keep_tif = tk.BooleanVar(value=True)
        self.var_threads = tk.IntVar(value=max(_threads_cfg, 0))
        for lab, var, w, tip in [
            ('组内最大间隔(秒)', self.var_gap, 6, '连拍组内相邻两帧的最大时间间隔；大于此间隔算新的一组'),
            ('每组最少帧数', self.var_min, 5, '少于此帧数的组不进表格（计入散照）'),
            ('每组最多用帧', self.var_topk, 5, '合成时每组最多用的帧数（自动选最锐的）'),
            ('放大倍率', self.var_scale, 5, '输出 = 输入分辨率 × 倍率'),
            ('锐化强度', self.var_sharpen, 5, '0-1，0 为不锐化'),
            ('核心数', self.var_threads, 5, '0=全部核心；填 8 只用 P 核，功耗温度大幅下降'),
        ]:
            ttk.Label(f3, text=lab).pack(side='left', padx=(10, 2))
            ttk.Entry(f3, textvariable=var, width=w).pack(side='left')

        # 复选框单独一行：单行 pack 不换行，塞进参数行会被窗宽裁掉（实测）
        f3b = ttk.Frame(self.root); f3b.pack(fill='x', **pad)
        ttk.Checkbutton(f3b, text='去鬼影', variable=self.var_deghost).pack(side='left', padx=(14, 2))
        # DNG 母版：LR 里 Kelvin 色温/相机配置/镜头矫正全可用（RAW 组生效，JPG 组自动回退 TIF）
        self.var_dng = tk.BooleanVar(value=True)
        ttk.Checkbutton(f3b, text='DNG母版(色温可调)', variable=self.var_dng).pack(side='left', padx=2)
        # 1.5x 压缩母版是 45MP 时代的选项（2x 输出上 GB 才需要）；24MP 默认直接留 2x 全尺寸 TIF
        self.var_master = tk.BooleanVar(value=False)
        ttk.Checkbutton(f3b, text='1.5x压缩母版TIF', variable=self.var_master).pack(side='left', padx=6)
        ttk.Checkbutton(f3b, text='另存全尺寸TIF', variable=self.var_keep_tif).pack(side='left', padx=2)
        # RL 反卷积锐化：只烙进 JPG 预览，母版 DNG 永远保持线性原样
        self.var_rl = tk.BooleanVar(value=True)
        ttk.Checkbutton(f3b, text='JPG反卷积锐化', variable=self.var_rl).pack(side='left', padx=(14, 2))
        # K14 外挂引擎（闭源，仅本机授权使用；商用/分发前须向作者确认许可）
        self.var_engine14 = tk.BooleanVar(value=False)
        self.var_k14 = tk.StringVar(value=_k14_cfg)
        ttk.Checkbutton(f3b, text='K14外挂引擎', variable=self.var_engine14).pack(side='left', padx=(14, 2))
        self._btn_k14 = ttk.Button(f3b, text='K14后端…', command=self._pick_k14)
        self._btn_k14.pack(side='left', padx=2)
        if self.var_k14.get():
            self._btn_k14.configure(text='K14后端✓')
        # K14 路线借鉴项：单应 ECC 精修（补透视项，残差不降 2% 自动退回欧氏）
        # + 实测残差融合加权（锐帧多信、糊帧少信）
        self.var_homo = tk.BooleanVar(value=True)
        ttk.Checkbutton(f3b, text='单应对齐(透视精修)', variable=self.var_homo).pack(side='left', padx=(14, 2))
        self.var_nwt = tk.BooleanVar(value=True)
        ttk.Checkbutton(f3b, text='噪声加权(锐帧多信)', variable=self.var_nwt).pack(side='left', padx=(14, 2))
        self.var_local = tk.BooleanVar(value=True)
        ttk.Checkbutton(f3b, text='局部对齐(视差校正)', variable=self.var_local).pack(side='left', padx=(14, 2))

        f4 = ttk.Frame(self.root); f4.pack(fill='x', **pad)
        ttk.Button(f4, text='① 扫描并判定', command=self._detect).pack(side='left', padx=4)
        ttk.Button(f4, text='② 合成选中组', command=self._run).pack(side='left', padx=4)
        btn_cancel = ttk.Button(f4, text='取消', command=self._cancel_scan)
        btn_cancel.pack(side='left', padx=4)
        btn_cancel.skip_set = True  # 扫描中其他按钮禁用时它必须可用
        self.var_status = tk.StringVar(value='就绪')
        ttk.Label(f4, textvariable=self.var_status, foreground='#0a7').pack(side='left', padx=16)

        self.var_summary = tk.StringVar(value='先「① 扫描并判定」，可超分的组默认勾选')
        ttk.Label(self.root, textvariable=self.var_summary,
                  foreground='#555').pack(anchor='w', padx=10)

        # 判定结果表（点击行切换勾选）
        cols = ('check', 'gi', 'time', 'frames', 'status', 'reason')
        self.tree = ttk.Treeview(self.root, columns=cols, show='headings', height=14)
        for cid, text, w, anchor in [
            ('check', '勾', 36, 'center'), ('gi', '组', 44, 'center'),
            ('time', '拍摄时间', 80, 'center'), ('frames', '帧数', 50, 'center'),
            ('status', '判定', 100, 'center'), ('reason', '说明', 520, 'w'),
        ]:
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=w, anchor=anchor, stretch=(cid == 'reason'))
        self.tree.tag_configure('ok', foreground='#0a7')
        self.tree.tag_configure('denoise', foreground='#369')
        self.tree.tag_configure('no', foreground='#c33')
        self.tree.tag_configure('done', foreground='#888')
        self.tree.pack(fill='both', expand=False, padx=10, pady=(4, 2))
        self.tree.bind('<ButtonRelease-1>', self._on_tree_click)

        self.txt = tk.Text(self.root, height=8)
        self.txt.pack(fill='both', expand=True, padx=10, pady=(2, 10))

    def _pick_src(self):
        p = filedialog.askdirectory(title='选择照片文件夹（RAW/JPG 连拍混放即可）')
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
                elif isinstance(msg, tuple) and msg[0] == '__VERDICTS__':
                    self._populate(msg[1], msg[2], msg[3])
                else:
                    self.log(msg)
        except queue.Empty:
            pass
        self.root.after(150, self._poll)

    def _set_buttons(self, on):
        for b in self.root.winfo_children():
            if isinstance(b, ttk.Frame):
                for c in b.winfo_children():
                    if isinstance(c, ttk.Button) and not getattr(c, 'skip_set', False):
                        c['state'] = 'normal' if on else 'disabled'

    def _cancel_scan(self):
        self._cancel = True
        self.var_status.set('正在取消…（保留已判定结果）')

    def _on_tree_click(self, _event):
        if not self.tree.selection():
            return
        iid = self.tree.selection()[0]
        gi = int(iid)
        values = list(self.tree.item(iid, 'values'))
        if gi in self.checked:
            self.checked.discard(gi)
            values[0] = '☐'
        else:
            self.checked.add(gi)
            values[0] = '☑'
        self.tree.item(iid, values=values)

    def _populate(self, verdicts, singles, cancelled):
        self.verdicts = verdicts
        self.checked = set()
        self.tree.delete(*self.tree.get_children())
        counts = {'ok': 0, 'denoise_only': 0, 'no': 0, 'done': 0}
        for gi, v in enumerate(verdicts, 1):
            label, tag = STATUS_DISPLAY[v.status]
            if v.status == 'ok':
                self.checked.add(gi)
            tstr = datetime.datetime.fromtimestamp(v.start_time).strftime('%H:%M:%S') \
                if v.start_time else '--:--:--'
            self.tree.insert('', 'end', iid=str(gi),
                             values=('☑' if gi in self.checked else '☐', gi, tstr,
                                     len(v.files), label, v.reason),
                             tags=(tag,))
            counts[v.status] += 1
        tail = '（已取消，仅显示已判定的组）' if cancelled else ''
        self.var_summary.set(
            f"可超分 {counts['ok']} 组 · 仅降噪 {counts['denoise_only']} · "
            f"不可 {counts['no']} · 已完成 {counts['done']}；另有 {singles} 张散照/小组 {tail}")

    def _detect(self):
        src = self.var_src.get().strip()
        if not Path(src).is_dir():
            messagebox.showerror('错误', '照片文件夹不存在')
            return
        # Tkinter 变量只能在主线程访问：参数先取好再交给工作线程
        dst = self.var_dst.get().strip()
        gap, min_frames = self.var_gap.get(), self.var_min.get()
        self.txt.delete('1.0', 'end')
        self.tree.delete(*self.tree.get_children())
        self.verdicts, self.checked = [], set()
        self.var_summary.set('扫描中…')
        self._cancel = False
        self.var_status.set('扫描判定中…')
        self._set_buttons(False)

        def work():
            try:
                self.q.put(f'扫描 {src} …（判定每组抽 8 帧，约几秒/组，可随时取消）')
                verdicts, singles = scan_and_judge(
                    src,
                    output_dir=dst or None,   # 给定输出目录时检测已合成过的组
                    gap=gap,
                    min_frames=min_frames,
                    progress_cb=self.q.put,
                    cancel_cb=lambda: self._cancel)
                self.q.put(('__VERDICTS__', verdicts, singles, self._cancel))
                self.q.put('__DONE__')
            except Exception:
                self.q.put('扫描失败:\n' + traceback.format_exc())
                self.q.put('__DONE__')
        threading.Thread(target=work, daemon=True).start()

    def _unique_base(self, dst, tstr, gi, scale, master, use_dng):
        """输出名冲突时追加 _2/_3（已合成的组重做时会走到这里）"""
        n = 1
        while True:
            suffix = '' if n == 1 else f'_{n}'
            base = Path(dst) / f'组{gi:02d}_{tstr}{suffix}'
            if use_dng:
                taken = [Path(f'{base}_{scale}x.dng'), Path(f'{base}_{scale}x.jpg')]
            else:
                taken = [Path(f'{base}_{scale}x.jpg'), Path(f'{base}.tif')]
                if master:
                    taken.append(Path(f'{base}_{master}x.tif'))
            if not any(p.exists() for p in taken):
                return base
            n += 1

    def _pick_k14(self):
        f = filedialog.askopenfilename(
            title='选择 Jiangtherapee 的 jsr_fast_k14_backend.exe',
            filetypes=[('jsr_fast_k14_backend', 'jsr_fast_k14_backend.exe'), ('所有文件', '*.*')])
        if f:
            self.var_k14.set(f)
            self._btn_k14.configure(text='K14后端✓')

    def _run(self):
        if not self.verdicts:
            messagebox.showinfo('提示', '请先「① 扫描并判定」')
            return
        sel = sorted(self.checked)
        if not sel:
            messagebox.showinfo('提示', '没有勾选任何组（点击表格行切换勾选）')
            return
        dst = self.var_dst.get().strip()
        if not dst:
            messagebox.showerror('错误', '请设置输出文件夹')
            return
        # Tkinter 变量只能在主线程访问：参数先取好再交给工作线程
        scale = self.var_scale.get()
        use_dng = self.var_dng.get()
        master = 1.5 if (self.var_master.get() and not use_dng) else None
        topk = self.var_topk.get()
        deghost = self.var_deghost.get()
        sharpen = self.var_sharpen.get()
        threads = self.var_threads.get()
        keep_tif = self.var_keep_tif.get()
        rl = self.var_rl.get()
        homo = self.var_homo.get()
        nwt = self.var_nwt.get()
        local_al = self.var_local.get()
        use_k14 = self.var_engine14.get()
        k14 = self.var_k14.get().strip() or None
        if use_k14 and not k14:
            messagebox.showerror('错误', 'K14外挂引擎未设置后端：点「K14后端…」选择 Bridge resources 目录下的 jsr_fast_k14_backend.exe')
            return
        if use_k14 and scale != 2:
            messagebox.showinfo('提示', 'K14 外挂引擎仅支持 2x，本次按 2x 执行')
            scale = 2
        Path(dst).mkdir(parents=True, exist_ok=True)
        try:
            _c2 = configparser.ConfigParser()
            _c2['run'] = {'threads': str(threads),
                          'k14_backend': self.var_k14.get().strip()}
            with open(os.path.join(_exe_dir, 'sr_gui.ini'), 'w', encoding='utf-8') as f:
                _c2.write(f)
        except Exception:
            pass
        self.var_status.set('合成中…')
        self._set_buttons(False)

        def work():
            try:
                import re
                from sr_process import process
                total = len(sel)
                for k, gi in enumerate(sel, 1):
                    v = self.verdicts[gi - 1]
                    fp = group_fingerprint(v.files)
                    tstr = datetime.datetime.fromtimestamp(v.start_time).strftime('%H%M%S') \
                        if v.start_time else f'{gi:02d}0000'
                    base = self._unique_base(dst, tstr, gi, scale, master, use_dng)
                    self.q.put(f'[{k}/{total}] 组{gi:02d} {len(v.files)}帧: 合成中…')
                    tmp = Path(dst) / f'.tmp_g{gi}'
                    tmp.mkdir(exist_ok=True)
                    for p in v.files:
                        shutil.copy(str(p), str(tmp / p.name))
                    buf = io_log()
                    try:
                        with Redirector(buf):
                            process(str(tmp), str(base) + '.tif', scale=scale,
                                    max_frames=topk,
                                    use_deghost=deghost,
                                    sharpen=sharpen,
                                    threads=threads,
                                    master_scale=master,
                                    output_dng=use_dng,
                                    rl_sharpen=rl,
                                    k14_backend=(k14 if use_k14 else None),
                                    use_homography=homo,
                                    use_noise_weight=nwt,
                                    use_local_align=local_al)
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
                    jpg = Path(f'{base}_{scale}x.jpg')
                    kept_master = None
                    dng_path = Path(f'{base}_{scale}x.dng')
                    if use_dng and dng_path.exists():
                        kept_master = dng_path
                    elif master:
                        p = Path(f'{base}_{master}x.tif')
                        kept_master = p if p.exists() else None
                    elif keep_tif:
                        p = Path(f'{base}.tif')
                        kept_master = p if p.exists() else None
                    else:
                        Path(str(base) + '.tif').unlink(missing_ok=True)
                    record_done(dst, fp, jpg, kept_master)
                    master_name = kept_master.name if kept_master else '无母版'
                    self.q.put(f'  完成 {t} -> {jpg.name} / {master_name}')
                self.q.put('全部完成！输出在: ' + dst)
                self.q.put('__DONE__')
            except Exception:
                self.q.put('失败:\n' + traceback.format_exc())
                self.q.put('__DONE__')
        threading.Thread(target=work, daemon=True).start()


def clean_tmp(tmp):
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
