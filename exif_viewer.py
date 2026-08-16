# -*- coding: utf-8 -*-
"""
EXIF 信息查看窗口（快门数 + 拍摄参数）

- 作为水印工具的子窗口打开（open_exif_window），也可独立运行（python exif_viewer.py [路径...]）
- 支持拖拽文件/文件夹、按钮选择，批量解析
- 解析引擎自动选择：ExifTool（完整）→ exifread（精简），见 exif_reader.py
"""
import queue
import threading
from pathlib import Path

from exif_reader import find_exiftool, read_exif_file

# 文件夹扫描时识别的图片扩展名
IMAGE_EXTS = {
    '.nef', '.jpg', '.jpeg', '.png', '.tif', '.tiff', '.bmp',
    '.arw', '.cr2', '.cr3', '.orf', '.rw2', '.pef', '.dng', '.raf',
    '.sr2', '.srw', '.heic', '.heif', '.webp',
}

# 表格列：(键, 标题, 宽度)
COLUMNS = [
    ('file', '文件名', 230),
    ('model', '机身', 150),
    ('shutter', '快门数', 70),
    ('exposure', '快门速度', 85),
    ('aperture', '光圈', 60),
    ('iso', 'ISO', 60),
    ('focal', '焦距', 70),
    ('datetime', '拍摄时间', 145),
    ('lens', '镜头', 210),
]

DEFAULT_RESULT = {
    'path': '', 'ok': False, 'engine': '',
    'model': '', 'shutter_count': None, 'lens': '', 'exposure': '',
    'aperture': '', 'iso': None, 'focal': '', 'datetime': '', 'error': '',
}


def collect_paths(inputs):
    """展开输入（文件/文件夹）为去重后的图片文件列表。"""
    files, seen = [], set()
    for p in inputs:
        p = Path(p)
        try:
            if p.is_file():
                key = str(p).lower()
                if key not in seen:
                    seen.add(key)
                    files.append(p)
            elif p.is_dir():
                for f in sorted(p.rglob('*')):
                    if f.is_file() and f.suffix.lower() in IMAGE_EXTS:
                        key = str(f).lower()
                        if key not in seen:
                            seen.add(key)
                            files.append(f)
        except OSError:
            continue
    return files


def _fmt_result(r):
    """把解析结果字典转成表格行值。"""
    err = r.get('error', '')
    if not r.get('ok'):
        name = Path(r.get('path', '')).name or r.get('path', '')
        return [f'⚠ {name}', err, '', '', '', '', '', '', '']
    sc = r.get('shutter_count')
    return [
        Path(r.get('path', '')).name,
        r.get('model', '') or '—',
        str(sc) if sc is not None else '—',
        r.get('exposure', '') or '—',
        r.get('aperture', '') or '—',
        str(r.get('iso', '')) if r.get('iso') is not None else '—',
        r.get('focal', '') or '—',
        r.get('datetime', '') or '—',
        r.get('lens', '') or '—',
    ]


class _ToolTip:
    """简易悬停提示（避免依赖主程序的 ToolTip 类）。"""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind('<Enter>', self._show, add='+')
        widget.bind('<Leave>', self._hide, add='+')

    def _show(self, _event=None):
        if self.tip is not None:
            return
        import tkinter as tk
        x, y, _, _ = self.widget.bbox('insert')
        x += self.widget.winfo_rootx() + 20
        y += self.widget.winfo_rooty() + 24
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f'+{x}+{y}')
        tk.Label(self.tip, text=self.text, justify='left',
                 bg='#ffffe8', relief='solid', borderwidth=1,
                 wraplength=520).pack()

    def _hide(self, _event=None):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


def open_exif_window(parent=None, initial_paths=None):
    """打开 EXIF 信息查看窗口。

    Args:
        parent: 主窗口（Toplevel 挂到其上）；None 时独立创建 Tk 根窗口
        initial_paths: 初始要解析的文件/文件夹路径列表
    """
    import tkinter as tk
    from tkinter import filedialog, font as tkfont, ttk

    try:
        from tkinterdnd2 import DND_FILES
        dnd_available = True
    except Exception:
        dnd_available = False

    if parent is not None:
        win = tk.Toplevel(parent)
    else:
        try:
            from tkinterdnd2 import TkinterDnD
            win = TkinterDnD.Tk()
        except Exception:
            win = tk.Tk()

    win.title('EXIF 信息查看（快门数） - Photo Watermark')
    win.configure(bg='#f5f7fa')
    win.geometry('1150x580')

    # 中文字体
    try:
        families = set(tkfont.families(win))
        for name in ('Microsoft YaHei UI', 'Microsoft YaHei', 'SimHei'):
            if name in families:
                tkfont.nametofont('TkDefaultFont').configure(family=name, size=10)
                break
    except Exception:
        pass
    base_font = tkfont.nametofont('TkDefaultFont')
    small_font = base_font.copy()
    small_font.configure(size=9)
    try:
        style = ttk.Style(win)
        try:
            style.theme_use('vista')
        except tk.TclError:
            pass
    except Exception:
        style = None

    exe = find_exiftool()
    engine_text = 'ExifTool 完整解析' if exe else '精简解析(exifread，部分机身快门数可能读不到)'

    # ===== 顶部工具栏 =====
    toolbar = ttk.Frame(win, padding=(10, 8))
    toolbar.pack(fill='x')
    ttk.Button(toolbar, text='选择文件（可多选）', command=lambda: _pick_files(),
               style='Primary.TButton').pack(side='left', padx=(0, 6))
    ttk.Button(toolbar, text='选择文件夹', command=lambda: _pick_folder(),
               style='Normal.TButton').pack(side='left', padx=(0, 6))
    ttk.Button(toolbar, text='清空', command=lambda: _clear()).pack(side='left', padx=(0, 6))
    ttk.Label(toolbar, text=engine_text, style='Desc.TLabel').pack(side='right')

    # ===== 表格 =====
    table_frame = ttk.Frame(win, padding=(10, 0))
    table_frame.pack(fill='both', expand=True)
    cols = [c[0] for c in COLUMNS]
    headers = [c[1] for c in COLUMNS]
    widths = {c[0]: c[2] for c in COLUMNS}
    tree = ttk.Treeview(table_frame, columns=cols, show='headings', height=16)
    for col, hdr in zip(cols, headers):
        tree.heading(col, text=hdr)
        tree.column(col, width=widths[col], anchor='w' if col == 'file' else 'center',
                    stretch=(col in ('file', 'lens', 'model', 'datetime')))
    vsb = ttk.Scrollbar(table_frame, orient='vertical', command=tree.yview)
    hsb = ttk.Scrollbar(table_frame, orient='horizontal', command=tree.xview)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    tree.grid(row=0, column=0, sticky='nsew')
    vsb.grid(row=0, column=1, sticky='ns')
    hsb.grid(row=1, column=0, sticky='ew')
    table_frame.rowconfigure(0, weight=1)
    table_frame.columnconfigure(0, weight=1)
    tree.tag_configure('err', foreground='#c0392b')
    tree.tag_configure('ok', foreground='#1e3a5f')

    # ===== 状态栏 =====
    status = ttk.Frame(win, padding=(10, 6))
    status.pack(fill='x')
    status_var = tk.StringVar(value='可拖入照片/文件夹，或点按钮选择')
    tk.Label(status, textvariable=status_var, font=small_font,
             bg='#f5f7fa', fg='#555555').pack(side='left')
    progress_var = tk.StringVar(value='')
    tk.Label(status, textvariable=progress_var, font=small_font,
             bg='#f5f7fa', fg='#2b579a').pack(side='right')

    # 拖放热区提示（表格上方的细条）
    drop_hint = tk.Label(win, text='⇩ 把 NEF / JPEG / 文件夹直接拖到这里 ⇩', font=small_font,
                         bg='#eaf1f8', fg='#4a6d9c', pady=4)
    drop_hint.pack(fill='x', padx=10)

    state = {
        'rows': {},      # iid -> result dict
        'busy': False,
        'q': queue.Queue(),
        'tooltip': None,
    }

    def _insert_row(r):
        values = _fmt_result(r)
        tag = 'ok' if r.get('ok') else 'err'
        iid = tree.insert('', 'end', values=values, tags=(tag,))
        state['rows'][iid] = r

    def _pick_files():
        files = filedialog.askopenfilenames(
            title='选择要查看 EXIF 的照片（可多选）',
            parent=win,
            filetypes=[('图片文件', '*.nef *.jpg *.jpeg *.png *.tif *.tiff *.bmp '
                                   '*.arw *.cr2 *.cr3 *.orf *.rw2 *.pef *.dng *.heic'),
                       ('所有文件', '*.*')],
        )
        if files:
            process(list(files))

    def _pick_folder():
        folder = filedialog.askdirectory(title='选择要批量查看的文件夹', parent=win)
        if folder:
            process([folder])

    def _clear():
        if state['busy']:
            return
        tree.delete(*tree.get_children())
        state['rows'].clear()
        status_var.set('已清空')
        progress_var.set('')

    def process(inputs):
        if state['busy']:
            return
        paths = collect_paths(inputs)
        if not paths:
            status_var.set('没有找到可识别的图片文件')
            return
        state['busy'] = True
        progress_var.set(f'0/{len(paths)}')
        status_var.set(f'正在解析 {len(paths)} 个文件…')
        threading.Thread(target=_worker, args=(paths, exe, state['q']),
                         daemon=True).start()
        _poll()

    def _worker(paths, exiftool, q):
        total = len(paths)
        for i, p in enumerate(paths, 1):
            try:
                r = read_exif_file(str(p), exiftool=exiftool)
            except Exception as e:  # noqa: BLE001
                r = dict(DEFAULT_RESULT, path=str(p), error=f'读取异常: {e}')
            q.put(('row', r, i, total))
        q.put(('done', total))

    def _poll():
        try:
            while True:
                msg = state['q'].get_nowait()
                if msg[0] == 'row':
                    _, r, i, total = msg
                    _insert_row(r)
                    progress_var.set(f'{i}/{total}')
                    ok_count = sum(1 for rr in state['rows'].values() if rr.get('ok'))
                    status_var.set(f'已解析 {i}/{total} 个文件，成功 {ok_count} 个')
                elif msg[0] == 'done':
                    total = msg[1]
                    state['busy'] = False
                    ok_count = sum(1 for rr in state['rows'].values() if rr.get('ok'))
                    if ok_count == total:
                        status_var.set(f'完成：共 {total} 个文件，全部解析成功')
                    else:
                        status_var.set(f'完成：共 {total} 个文件，成功 {ok_count} 个，'
                                       f'失败 {total - ok_count} 个（红色行，双击查看原因）')
                    progress_var.set('')
        except queue.Empty:
            pass
        if state['busy']:
            win.after(100, _poll)

    def _show_detail(_event=None):
        iid = tree.focus()
        if not iid or iid not in state['rows']:
            return
        r = state['rows'][iid]
        dlg = tk.Toplevel(win)
        dlg.title(Path(r.get('path', '')).name)
        dlg.configure(bg='#f5f7fa')
        dlg.geometry('560x420')
        txt = tk.Text(dlg, wrap='word', font=('Microsoft YaHei UI', 10),
                      padx=12, pady=10)
        txt.pack(fill='both', expand=True, padx=10, pady=10)
        lines = [f'文件: {r.get("path", "")}']
        if not r.get('ok'):
            lines.append(f'状态: 解析失败')
            lines.append(f'原因: {r.get("error", "未知错误")}')
        else:
            lines += [
                f'机身: {r.get("model", "") or "—"}',
                f'快门数: {r.get("shutter_count", "") or "—"}',
                f'快门速度: {r.get("exposure", "") or "—"}',
                f'光圈: {r.get("aperture", "") or "—"}',
                f'ISO: {r.get("iso", "") or "—"}',
                f'焦距: {r.get("focal", "") or "—"}',
                f'拍摄时间: {r.get("datetime", "") or "—"}',
                f'镜头: {r.get("lens", "") or "—"}',
                f'解析引擎: {r.get("engine", "")}',
            ]
        txt.insert('1.0', '\n'.join(lines))
        txt.configure(state='disabled')

    tree.bind('<Double-1>', _show_detail)
    tk.Label(status, text='双击行查看完整信息', font=small_font,
             bg='#f5f7fa', fg='#999999').pack(side='left', padx=(14, 0))

    # 拖放
    def _on_drop(event):
        try:
            paths = win.tk.splitlist(event.data)
        except Exception:
            paths = [p for p in event.data.split() if p]
        if paths:
            process(paths)

    if dnd_available:
        win.drop_target_register(DND_FILES)
        win.dnd_bind('<<Drop>>', _on_drop)
        drop_hint.configure(text='⇩ 把 NEF / JPEG / 文件夹直接拖到这里 ⇩（已启用拖放）')
    else:
        drop_hint.configure(text='（当前环境不支持拖放，请用按钮选择文件）')

    # 初始路径
    if initial_paths:
        process(initial_paths)

    win.lift()
    win.focus_force()
    return win


if __name__ == '__main__':
    import sys
    args = sys.argv[1:]
    win = open_exif_window(parent=None, initial_paths=args or None)
    win.mainloop()
