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
_threads_cfg = _cfg.getint('run', 'threads', fallback=0)
_k14_cfg = _cfg.get('run', 'k14_backend', fallback='')
if _threads_cfg > 0:
    for _v in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ[_v] = str(_threads_cfg)

import datetime
import io
import queue
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


class ToolTip:
    """悬停提示：鼠标停 800ms 弹出说明气泡（术语解释不占界面）"""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self._tip = None
        self._after = None
        widget.bind('<Enter>', self._schedule)
        widget.bind('<Leave>', self._hide)
        widget.bind('<ButtonPress>', self._hide)

    def _schedule(self, _e):
        self._hide()
        self._after = self.widget.after(800, self._show)

    def _show(self):
        if self._tip is not None:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f'+{x}+{y}')
        tk.Label(tw, text=self.text, justify='left', bg='#ffffe0', fg='#333',
                 relief='solid', borderwidth=1, wraplength=460,
                 font=('Microsoft YaHei UI', 9)).pack()

    def _hide(self, _e=None):
        if self._after is not None:
            self.widget.after_cancel(self._after)
            self._after = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class App:
    def __init__(self, root):
        self.root = root
        root.title('手持多帧超分辨率合成（连拍分组版）')
        root.geometry('1240x780')
        self.q = queue.Queue()
        self.verdicts = []          # sr_suggest.Verdict 列表（与表格行号一一对应）
        self.checked = set()        # 勾选的组号（1 起）
        self._cancel = False
        self._scanning = False      # 取消按钮只对扫描有意义
        self._work = None           # 当前工作线程句柄（关窗确认用）
        self._build()
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self.root.after(100, self._poll)

    def _on_close(self):
        """合成/扫描进行中关窗会杀掉 daemon 工作线程，可能留下半成品文件"""
        if self._work is not None and self._work.is_alive():
            if not messagebox.askyesno(
                    '确认关闭', '正在扫描/合成，关闭会中断任务，\n'
                    '可能留下不完整的输出文件。仍要关闭？'):
                return
        self.root.destroy()

    def _build(self):
        # 深蓝标题栏：与主窗口 / 清糊片窗口同一套视觉（三窗一致）
        head = tk.Frame(self.root, bg='#1e3a5f')
        head.pack(fill='x')
        tk.Label(head, text='🔬 超分合成', font=('Microsoft YaHei UI', 15, 'bold'),
                 bg='#1e3a5f', fg='#ffffff').pack(side='left', padx=14, pady=10)
        tk.Label(head, text='手持连拍多帧超分辨率：扫描分组 → 勾选 → 合成（DNG底片 + 2x JPG）',
                 font=('Microsoft YaHei UI', 9), bg='#1e3a5f', fg='#9db4d0').pack(side='left')
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
        self.var_topk = tk.IntVar(value=12)
        self.var_scale = tk.IntVar(value=2)
        self.var_sharpen = tk.DoubleVar(value=0.3)
        self.var_deghost = tk.BooleanVar(value=True)
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
            e = ttk.Entry(f3, textvariable=var, width=w)
            e.pack(side='left')
            ToolTip(e, tip)

        # 复选框单独一行：单行 pack 不换行，塞进参数行会被窗宽裁掉（实测）
        f3b = ttk.Frame(self.root); f3b.pack(fill='x', **pad)
        c = ttk.Checkbutton(f3b, text='去鬼影', variable=self.var_deghost)
        c.pack(side='left', padx=(14, 2))
        ToolTip(c, '多张连拍自动去掉画面里移动/变化的部分（人物走动等），避免重影')
        # DNG 底片：LR 里色温/相机配置/镜头矫正全可用（RAW 组生效，JPG 组自动回退 TIF）
        self.var_dng = tk.BooleanVar(value=True)
        c = ttk.Checkbutton(f3b, text='DNG底片(色温可调)', variable=self.var_dng)
        c.pack(side='left', padx=2)
        ToolTip(c, '输出类似"数码底片"的 DNG：白平衡、色温、镜头矫正全部留到 Lightroom 里随便调。\n'
                   'JPG 素材的组不支持，自动改出 TIF')
        # 母版倍率：融合始终按 2x 超采样（亚像素位移落在 2x 网格的不同位置，
        # 这是降噪/去锯齿的来源），母版再按此倍率线性域降采样省空间——
        # 天气/镜头限制实际分辨率时，2x 母版纯属浪费磁盘。2.0=不缩（全尺寸）
        self.var_master = tk.StringVar(value='1.5')
        c = ttk.Label(f3b, text='母版倍率')
        c.pack(side='left', padx=(6, 0))
        sb = ttk.Spinbox(f3b, from_=1.0, to=2.0, increment=0.1, width=4,
                         textvariable=self.var_master)
        sb.pack(side='left', padx=(2, 0))
        ToolTip(sb, '融合固定按 2x 超采样，母版按此倍率在线性域降采样（1.0–2.0）。\n'
                    '1.5 = 4×6 照片出 6×9 档，体积约为 2x 的 56%；被天气/镜头限制\n'
                    '实际分辨率时降到 1.2–1.4 更省；2.0 = 不缩（全尺寸母版）。\n'
                    'DNG 底片模式下母版是线性 DNG（可调色温），否则是 16bit 压缩 TIF；\n'
                    '填 2.0 = 不缩的全尺寸母版（体积大，JPG 组为全尺寸 TIF）。\n'
                    '2x JPG 始终按全尺寸输出，分享/查看用母版看 DNG/TIF')
        tk.Label(f3b, text='x', foreground='#555').pack(side='left')
        # （2026-10-26 撤掉「另存全尺寸TIF」勾选框：默认开着一个几百 MB~1GB 的
        # 无压缩产物违背"默认适合大多数人"；母版倍率=2.0 就是显式要全尺寸母版，
        # 勍选框逻辑已名存实亡——<2 时母版按倍率缩、=2 时母版即全尺寸）
        # JPG 锐化：只影响 JPG，DNG 底片永远保持原样
        self.var_rl = tk.BooleanVar(value=True)
        c = ttk.Checkbutton(f3b, text='JPG更锐利', variable=self.var_rl)
        c.pack(side='left', padx=(14, 2))
        ToolTip(c, '输出的 JPG 先做智能锐化，看起来更锐；只影响 JPG，DNG 底片保持原始数据')
        # 合成完自动出双图对比：中位锐度单帧 vs 合成（同一渲染管线），
        # 放到 SR输出 同级的 SR对比图/<批次>/，不进 SR输出（LR 会整目录扫描）
        self.var_compare = tk.BooleanVar(value=True)
        c = ttk.Checkbutton(f3b, text='出对比图(单帧vs合成)', variable=self.var_compare)
        c.pack(side='left', padx=(14, 2))
        ToolTip(c, '合成完成后自动生成一张双图对比：中位锐度单帧 vs 合成结果，\n'
                   '同一渲染管线（色调可比），输出到 SR输出 同级的 SR对比图/<批次>/ 目录。')

        # 第二行：画质增强 + 外部引擎（语义分组，也不再受窗宽裁切）
        f3c = ttk.Frame(self.root); f3c.pack(fill='x', **pad)
        # 画质增强三项合一：透视精修对齐 + 逐块视差校正 + 清晰帧智能加权，
        # 全部默认最优、各自带自动兜底（不适用时自动退回保守模式），无需分别关
        self.var_enhance = tk.BooleanVar(value=True)
        c = ttk.Checkbutton(f3c, text='画质增强(推荐)', variable=self.var_enhance)
        c.pack(side='left', padx=(14, 2))
        ToolTip(c, '三项自动画质增强：更准的画面对齐（透视+局部）、清晰帧多出力模糊帧少出力。\n'
                   '全部自动兜底，遇到不适用的情况会自己退回保守模式；个别画质异常时才需要关掉。\n'
                   '需要单独控制请用命令行参数 --no-homography / --no-noise-weight / --no-local-align')
        # K14 外部 AI 引擎（闭源，仅本机授权使用；商用/分发前须向作者确认许可）
        self.var_engine14 = tk.BooleanVar(value=False)
        self.var_k14 = tk.StringVar(value=_k14_cfg)
        c = ttk.Checkbutton(f3c, text='K14增强(AI)', variable=self.var_engine14)
        c.pack(side='left', padx=(14, 2))
        ToolTip(c, '改用外部 K14 神经网络引擎合成（效果更好，需本机装有 JSR/Bridge 并指定位置）。\n'
                   '仅支持 2x、组内至少 14 帧（不足自动改用内置管线）；该引擎是他人的闭源软件，仅限本机自己使用')
        self._btn_k14 = ttk.Button(f3c, text='K14后端…', command=self._pick_k14)
        self._btn_k14.pack(side='left', padx=2)
        ToolTip(self._btn_k14, '选择 K14 引擎程序的位置（JSR/Bridge 目录 resources 下的\n'
                               'jsr_fast_k14_backend.exe）；选过一次会记住')
        if self.var_k14.get():
            self._btn_k14.configure(text='K14后端✓')

        f4 = ttk.Frame(self.root); f4.pack(fill='x', **pad)
        ttk.Button(f4, text='① 扫描并判定', command=self._detect).pack(side='left', padx=4)
        ttk.Button(f4, text='② 合成选中组', command=self._run).pack(side='left', padx=4)
        btn_cancel = ttk.Button(f4, text='取消', command=self._cancel_scan)
        btn_cancel.pack(side='left', padx=4)
        btn_cancel.skip_set = True  # 扫描中其他按钮禁用时它必须可用
        self.btn_cancel = btn_cancel
        self.var_status = tk.StringVar(value='就绪')
        ttk.Label(f4, textvariable=self.var_status, foreground='#0a7').pack(side='left', padx=16)
        # 合成进度条：逐组推进（此前合成几分钟内状态栏只有一句"合成中…"）
        self.pbar = ttk.Progressbar(f4, length=200, mode='determinate')
        self.pbar.pack(side='right', padx=8)

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
                    self._scanning = False
                    self.pbar.config(value=0)
                    self.btn_cancel.state(['normal'])
                    self._set_buttons(True)
                elif isinstance(msg, tuple) and msg[0] == '__VERDICTS__':
                    self._populate(msg[1], msg[2], msg[3])
                elif isinstance(msg, tuple) and msg[0] == 'status':
                    self.var_status.set(msg[1])      # \r 覆写式进度（融合: 3/48 块）
                elif isinstance(msg, tuple) and msg[0] == 'pb':
                    self.pbar.config(value=msg[1])   # 合成逐组推进
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
        if not self._scanning:
            return                   # 合成阶段没有取消钩子，别让状态栏误导
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
        try:
            gap, min_frames = self.var_gap.get(), self.var_min.get()
        except tk.TclError:
            messagebox.showerror('参数错误', '组内最大间隔 / 每组最少帧数必须是数字')
            return
        self.txt.delete('1.0', 'end')
        self.tree.delete(*self.tree.get_children())
        self.verdicts, self.checked = [], set()
        self.var_summary.set('扫描中…')
        self._cancel = False
        self._scanning = True
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
        self._work = threading.Thread(target=work, daemon=True)
        self._work.start()

    def _unique_base(self, dst, tstr, gi, scale, master, use_dng):
        """输出名冲突时追加 _2/_3（已合成的组重做时会走到这里）"""
        n = 1
        while True:
            suffix = '' if n == 1 else f'_{n}'
            base = Path(dst) / f'组{gi:02d}_{tstr}{suffix}'
            taken = [Path(f'{base}_{scale}x.jpg')]
            if master:
                # DNG 勾选但 JPG 组会回退 TIF：两种后缀都算占用
                taken += [Path(f'{base}_{master}x.dng'), Path(f'{base}_{master}x.tif')]
            elif use_dng:
                taken += [Path(f'{base}_{scale}x.dng'), Path(f'{base}.tif')]
            else:
                taken.append(Path(f'{base}.tif'))
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
        try:
            scale = self.var_scale.get()
            topk = self.var_topk.get()
            sharpen = self.var_sharpen.get()
            threads = self.var_threads.get()
        except tk.TclError:
            messagebox.showerror('参数错误', '倍率 / 帧数上限 / 锐化 / 线程数必须是数字')
            return
        use_dng = self.var_dng.get()
        try:
            master_v = float(self.var_master.get())
        except (ValueError, tk.TclError):
            messagebox.showerror('参数错误', '母版倍率必须是 1.0 ~ 2.0 之间的数字')
            return
        if not 1.0 <= master_v <= 2.0:
            messagebox.showerror('参数错误', '母版倍率请在 1.0 ~ 2.0 之间（2.0 = 不缩的全尺寸母版）')
            return
        # 2.0 == 输出倍率：process 视为不缩母版；命名标签与 process 的
        # f'{ms:g}x' 口径一致（1.5→'1.5x'、1.2→'1.2x'、2.0 走全尺寸分支）
        master = None if master_v >= scale else master_v
        master_lbl = f'{master_v:g}'
        deghost = self.var_deghost.get()
        rl = self.var_rl.get()
        enhance = self.var_enhance.get()
        compare_out = self.var_compare.get()
        use_k14 = self.var_engine14.get()
        k14 = self.var_k14.get().strip() or None
        if use_k14 and not k14:
            messagebox.showerror('错误', 'K14外挂引擎未设置后端：点「K14后端…」选择 Bridge resources 目录下的 jsr_fast_k14_backend.exe')
            return
        if use_k14 and scale != 2:
            messagebox.showinfo('提示', 'K14 外挂引擎仅支持 2x，本次按 2x 执行')
            scale = 2
            self.var_scale.set(2)     # 回写界面，避免显示与实际倍率不一致
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
        self.btn_cancel.state(['disabled'])   # 合成没有取消钩子，别留可点的取消
        self.pbar.config(maximum=len(sel), value=0)

        def work():
            try:
                import contextlib
                import re
                from sr_process import process
                total = len(sel)
                for k, gi in enumerate(sel, 1):
                    v = self.verdicts[gi - 1]
                    fp = group_fingerprint(v.files)
                    tstr = datetime.datetime.fromtimestamp(v.start_time).strftime('%H%M%S') \
                        if v.start_time else f'{gi:02d}0000'
                    base = self._unique_base(dst, tstr, gi, scale,
                                             master_lbl if master else None, use_dng)
                    self.q.put(f'[{k}/{total}] 组{gi:02d} {len(v.files)}帧: 合成中…')
                    self.q.put(('pb', k - 1))
                    buf = _LiveLog(self.q)      # 实时日志：完成行进日志、\r 进度进状态栏
                    try:
                        with contextlib.redirect_stdout(buf):
                            # 文件清单直传：不再拷贝临时文件夹（省时省盘）
                            process(files=list(v.files), output_path=str(base) + '.tif',
                                    scale=scale,
                                    max_frames=topk,
                                    use_deghost=deghost,
                                    sharpen=sharpen,
                                    threads=threads,
                                    master_scale=master,
                                    output_dng=use_dng,
                                    rl_sharpen=rl,
                                    k14_backend=(k14 if use_k14 else None),
                                    use_homography=enhance,
                                    use_noise_weight=enhance,
                                    use_local_align=enhance,
                                    compare_out=compare_out)
                        m = re.search(r'耗时: ([\d.]+)', buf.getvalue())
                        t = f'{float(m.group(1)):.0f}s' if m else '?'
                    except ValueError as e:
                        self.q.put(f'  [跳过] {e}')
                        continue
                    except Exception as e:
                        self.q.put(f'  [失败] {e}')
                        continue
                    finally:
                        self.q.put(('pb', k))
                    jpg = Path(f'{base}_{scale}x.jpg')
                    kept_master = None
                    if master:
                        # DNG 组出 .dng，JPG 组回退 .tif：按实际存在取
                        cands = [Path(f'{base}_{master_lbl}x.dng'),
                                 Path(f'{base}_{master_lbl}x.tif')]
                        kept_master = next((p for p in cands if p.exists()), None)
                    elif use_dng:
                        p = Path(f'{base}_{scale}x.dng')
                        kept_master = p if p.exists() else None
                    else:
                        # 母版倍率 2.0 的非 DNG 组：全尺寸 TIF 就是用户要的母版，
                        # 无条件保留（不再有"选了 2.0 却被删掉"的矛盾）
                        p = Path(f'{base}.tif')
                        kept_master = p if p.exists() else None
                    record_done(dst, fp, jpg, kept_master)
                    master_name = kept_master.name if kept_master else '无母版'
                    self.q.put(f'  完成 {t} -> {jpg.name} / {master_name}')
                self.q.put('全部完成！输出在: ' + dst)
                self.q.put('__DONE__')
            except Exception:
                self.q.put('失败:\n' + traceback.format_exc())
                self.q.put('__DONE__')
        self._work = threading.Thread(target=work, daemon=True)
        self._work.start()



class _LiveLog(io.StringIO):
    """process() 的 stdout 双写：既留缓冲（供"耗时"正则），又实时进 GUI。

    - 完成行（不含 \\r）→ 队列 → 日志区实时滚动（此前整段吞进缓冲，
      合成几分钟内界面毫无反馈）；
    - \\r 覆写式进度（"融合: 3/48 块"）→ ('status', ...) → 状态栏单行刷新，
      不进日志区防刷屏。
    print 的一次调用可能拆成多次 write，按片段判断即可：\\r 进度总是
    独立片段开头。"""

    def __init__(self, q):
        super().__init__()
        self._q = q

    def write(self, s):
        super().write(s)
        if s.startswith('\r'):
            self._q.put(('status', s.strip() or '…'))
        elif s.strip():
            self._q.put(s.rstrip())
        return len(s)


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
