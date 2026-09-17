from __future__ import annotations

import ctypes
import os
import sqlite3
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont
from urllib.parse import quote

from .core import Account, Engine, Settings, SyncError

BG = '#F8FAFC'
CARD = '#FFFFFF'
TEXT = '#1E293B'
MUTED = '#475569'
PRIMARY = '#2563EB'
PRIMARY_DARK = '#1D4ED8'
BORDER = '#E2E8F0'
WARNING = '#B91C1C'
UI_FONT = 'Microsoft YaHei UI'
MONO_FONT = 'Consolas'

MESSAGES = {
    'disabled': '自动同步未开启。可以先预览，再同步一次。',
    'waiting': '恢复归属前请完全退出 WorkBuddy（包括托盘和后台）。',
    'switching': 'WorkBuddy 正在切换账号，登录信息稳定后会自动继续。',
    'busy': 'WorkBuddy 正在写入本地数据库，下一轮会自动继续。',
    'logged_out': '未检测到已登录账号，请先在 WorkBuddy 中完成登录。',
    'current': '所选会话已归当前账号，可在 WorkBuddy 中继续对话。',
    'synced': '同步完成。侧栏未刷新时，请选择会话并点击“在 WorkBuddy 中打开”。',
    'restored': '会话归属已恢复，自动同步已关闭。',
}


def short_id(value: str) -> str:
    return value if len(value) <= 14 else value[:8] + '…' + value[-4:]


def display_title(value: str | None) -> str:
    return ' '.join(value.split()) if value else '未命名会话'


def enable_windows_dpi_awareness():
    if os.name == 'nt':
        ctypes.windll.shcore.SetProcessDpiAwareness(2)


def configure_default_fonts(root):
    for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkCaptionFont'):
        tkfont.nametofont(name, root=root).configure(family=UI_FONT, size=10)
    tkfont.nametofont('TkHeadingFont', root=root).configure(
        family=UI_FONT, size=10, weight='bold')
    tkfont.nametofont('TkFixedFont', root=root).configure(family=MONO_FONT, size=9)


class CellTooltip:
    def __init__(self, tree: ttk.Treeview, resolver):
        self.tree = tree
        self.resolver = resolver
        self.tip = None
        self.key = None
        tree.bind('<Motion>', self._move, add='+')
        tree.bind('<Leave>', self.hide, add='+')
        tree.bind('<ButtonPress>', self.hide, add='+')

    def _column_name(self, column: str):
        if not column.startswith('#') or column == '#0':
            return ''
        displayed = self.tree.cget('displaycolumns')
        if displayed == '#all':
            displayed = self.tree.cget('columns')
        displayed = self.tree.tk.splitlist(displayed)
        index = int(column[1:]) - 1
        return displayed[index] if 0 <= index < len(displayed) else ''

    def _move(self, event):
        item = self.tree.identify_row(event.y)
        column = self._column_name(self.tree.identify_column(event.x))
        key = (item, column)
        if key == self.key:
            return
        self.hide()
        self.key = key
        text = self.resolver(item, column) if item and column else ''
        if not text:
            return
        self.tip = tk.Toplevel(self.tree)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f'+{event.x_root + 12}+{event.y_root + 18}')
        tk.Label(
            self.tip, text=text, background='#0F172A', foreground='#FFFFFF',
            padx=9, pady=5, font=(UI_FONT, 9),
        ).pack()

    def hide(self, _event=None):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None
        self.key = None


class Window:
    def __init__(self, root, config_path: Path):
        self.root = root
        self.config_path = config_path
        self.cfg = Settings.load(config_path)
        self.engine = Engine(self.cfg)
        self.last_backup = None
        self.selected_sessions = set(self.cfg.session_ids or [])
        self.session_owner_ids = {}
        self.accounts_by_id: dict[str, Account] = {}
        self.current_account_full_id = ''

        root.title('WorkBuddy 会话同步')
        root.geometry('1180x820')
        root.minsize(980, 700)
        root.configure(background=BG)
        configure_default_fonts(root)
        self._configure_styles()

        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        shell = ttk.Frame(root, style='App.TFrame', padding=(24, 20, 24, 16))
        shell.grid(sticky='nsew')
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(2, weight=1)

        header = ttk.Frame(shell, style='App.TFrame')
        header.grid(row=0, column=0, sticky='ew', pady=(0, 16))
        ttk.Label(header, text='WorkBuddy 会话同步', style='Title.TLabel').pack(anchor='w')
        ttk.Label(
            header, text='切换账号后继续同一段本地对话', style='Subtitle.TLabel',
        ).pack(anchor='w', pady=(4, 0))

        nav = ttk.Frame(shell, style='App.TFrame')
        nav.grid(row=1, column=0, sticky='w', pady=(0, 10))
        self.nav_buttons = []
        for index, title in enumerate(('同步概览', '账号历史', '设置与恢复')):
            button = ttk.Button(
                nav, text=title, width=14, style='Nav.TButton',
                command=lambda page=index: self.show_page(page),
            )
            button.pack(side='left', padx=(0, 6))
            self.nav_buttons.append(button)

        self.notebook = ttk.Notebook(shell, style='App.TNotebook')
        self.notebook.grid(row=2, column=0, sticky='nsew')
        self.sync_page = ttk.Frame(self.notebook, style='App.TFrame', padding=(0, 16, 0, 0))
        self.history_page = ttk.Frame(
            self.notebook, style='App.TFrame', padding=(0, 16, 0, 0))
        self.settings_page = ttk.Frame(
            self.notebook, style='App.TFrame', padding=(0, 16, 0, 0))
        self.notebook.add(self.sync_page, text='同步概览')
        self.notebook.add(self.history_page, text='账号历史')
        self.notebook.add(self.settings_page, text='设置与恢复')
        self.show_page(0)

        self._build_sync_page()
        self._build_history_page()
        self._build_settings_page()

        self.status = tk.StringVar(value=MESSAGES['disabled'])
        status_frame = tk.Frame(shell, background='#EFF6FF', padx=14, pady=10)
        status_frame.grid(row=3, column=0, sticky='ew', pady=(14, 0))
        self.status_label = tk.Label(
            status_frame, textvariable=self.status, background='#EFF6FF', foreground='#1E40AF',
            anchor='w', justify='left', font=(UI_FONT, 10),
        )
        self.status_label.pack(fill='x')

        self.refresh()
        root.after(self.cfg.poll_seconds * 1000, self.poll)

    def _configure_styles(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', font=(UI_FONT, 10), foreground=TEXT)
        style.configure('App.TFrame', background=BG)
        style.configure('Card.TFrame', background=CARD, relief='solid', borderwidth=1)
        style.configure('CardContent.TFrame', background=CARD, relief='flat', borderwidth=0)
        style.configure('Card.TSeparator', background=BORDER)
        style.configure('Title.TLabel', background=BG, foreground='#0F172A',
                        font=(UI_FONT, 24, 'bold'))
        style.configure('Subtitle.TLabel', background=BG, foreground=MUTED,
                        font=(UI_FONT, 10))
        style.configure('CardTitle.TLabel', background=CARD, foreground=MUTED,
                        font=(UI_FONT, 9))
        style.configure('AccountName.TLabel', background=CARD, foreground='#0F172A',
                        font=(UI_FONT, 16, 'bold'))
        style.configure('Metric.TLabel', background=CARD, foreground='#0F172A',
                        font=(UI_FONT, 24, 'bold'))
        style.configure('CardBody.TLabel', background=CARD, foreground=TEXT)
        style.configure('MutedCard.TLabel', background=CARD, foreground=MUTED)
        style.configure('Mono.TLabel', background=CARD, foreground=MUTED,
                        font=(MONO_FONT, 9))
        style.configure('Guide.TLabel', background='#EFF6FF', foreground='#1E40AF')
        style.configure('App.TNotebook', background=BG, borderwidth=0)
        style.layout('App.TNotebook.Tab', [])
        style.configure('Nav.TButton', background='#E2E8F0', foreground=MUTED,
                        borderwidth=1, padding=(14, 9), font=(UI_FONT, 10, 'bold'))
        style.map('Nav.TButton', background=[('active', '#CBD5E1')])
        style.configure('NavSelected.TButton', background=CARD, foreground=PRIMARY,
                        bordercolor=PRIMARY, borderwidth=1, padding=(14, 9),
                        font=(UI_FONT, 10, 'bold'))
        style.map('NavSelected.TButton', background=[('active', CARD)])
        style.configure('ToggleOn.TButton', background=PRIMARY, foreground='#FFFFFF',
                        borderwidth=1, padding=(14, 8), font=(UI_FONT, 10, 'bold'))
        style.map('ToggleOn.TButton', background=[('active', PRIMARY_DARK)])
        style.configure('ToggleOff.TButton', background=CARD, foreground=MUTED,
                        bordercolor='#CBD5E1', borderwidth=1, padding=(14, 8),
                        font=(UI_FONT, 10, 'bold'))
        style.map('ToggleOff.TButton', background=[('active', '#F1F5F9')])
        style.configure('Segment.TButton', background=CARD, foreground=MUTED,
                        bordercolor='#CBD5E1', borderwidth=1, padding=(14, 8))
        style.map('Segment.TButton', background=[('active', '#F1F5F9')])
        style.configure('SegmentSelected.TButton', background='#DBEAFE', foreground='#1E40AF',
                        bordercolor=PRIMARY, borderwidth=1, padding=(14, 8),
                        font=(UI_FONT, 10, 'bold'))
        style.map('SegmentSelected.TButton', background=[('active', '#BFDBFE')])
        style.configure('Primary.TButton', background=PRIMARY, foreground='#FFFFFF',
                        borderwidth=0, padding=(17, 9), font=(UI_FONT, 10, 'bold'))
        style.map('Primary.TButton', background=[('active', PRIMARY_DARK)])
        style.configure('Secondary.TButton', background=CARD, foreground=TEXT,
                        bordercolor='#CBD5E1', borderwidth=1, padding=(14, 8))
        style.map('Secondary.TButton', background=[('active', '#F1F5F9')])
        style.configure('Warning.TButton', background='#FEF2F2', foreground=WARNING,
                        bordercolor='#FCA5A5', borderwidth=1, padding=(14, 8),
                        font=(UI_FONT, 10, 'bold'))
        style.map('Warning.TButton', background=[('active', '#FEE2E2')])
        style.configure('Treeview', background=CARD, fieldbackground=CARD, foreground=TEXT,
                        rowheight=34, bordercolor=BORDER, borderwidth=1)
        style.configure('Treeview.Heading', background='#F1F5F9', foreground=MUTED,
                        font=(UI_FONT, 9, 'bold'), relief='flat', padding=(8, 8))
        style.map('Treeview', background=[('selected', '#DBEAFE')],
                  foreground=[('selected', '#1E3A8A')])

    def _card(self, parent, **grid):
        frame = ttk.Frame(parent, style='Card.TFrame', padding=16)
        frame.grid(**grid)
        return frame

    def show_page(self, index):
        self.notebook.select(index)
        for button_index, button in enumerate(self.nav_buttons):
            button.configure(
                style='NavSelected.TButton' if button_index == index else 'Nav.TButton')

    def _build_sync_page(self):
        page = self.sync_page
        page.columnconfigure(0, weight=1)
        page.rowconfigure(3, weight=1)

        guide = tk.Frame(page, background='#EFF6FF', padx=14, pady=10)
        guide.grid(row=0, column=0, sticky='ew', pady=(0, 12))
        tk.Label(
            guide,
            text='开启自动同步并保持本窗口运行；在 WorkBuddy 内切换账号后，等待下方状态提示同步完成。',
            background='#EFF6FF', foreground='#1E40AF', anchor='w',
            font=(UI_FONT, 10),
        ).pack(fill='x')

        metrics = ttk.Frame(page, style='App.TFrame')
        metrics.grid(row=1, column=0, sticky='ew', pady=(0, 12))
        metrics.columnconfigure(0, weight=2)
        metrics.columnconfigure(1, weight=1)
        metrics.columnconfigure(2, weight=1)

        account_card = self._card(metrics, row=0, column=0, sticky='nsew', padx=(0, 8))
        self.current_account_name = tk.StringVar(value='未登录')
        self.current_account_id = tk.StringVar(value='—')
        ttk.Label(account_card, text='当前登录账号', style='CardTitle.TLabel').pack(anchor='w')
        ttk.Label(
            account_card, textvariable=self.current_account_name, style='AccountName.TLabel',
        ).pack(anchor='w', pady=(7, 3))
        ttk.Label(
            account_card, textvariable=self.current_account_id, style='Mono.TLabel',
        ).pack(anchor='w')

        sessions_card = self._card(metrics, row=0, column=1, sticky='nsew', padx=4)
        self.available_count = tk.StringVar(value='0')
        ttk.Label(sessions_card, text='可用会话', style='CardTitle.TLabel').pack(anchor='w')
        ttk.Label(
            sessions_card, textvariable=self.available_count, style='Metric.TLabel',
        ).pack(anchor='w', pady=(8, 0))

        shared_card = self._card(metrics, row=0, column=2, sticky='nsew', padx=(8, 0))
        self.shared_count = tk.StringVar(value='0')
        ttk.Label(shared_card, text='共享范围', style='CardTitle.TLabel').pack(anchor='w')
        ttk.Label(shared_card, textvariable=self.shared_count, style='Metric.TLabel').pack(
            anchor='w', pady=(8, 0))

        scope = self._card(page, row=2, column=0, sticky='ew', pady=(0, 12))
        self.scope_card = scope
        scope.columnconfigure(0, weight=1)
        self.auto = tk.BooleanVar(value=self.cfg.auto_sync)
        self.all_sessions = tk.BooleanVar(value=self.cfg.session_ids is None)
        auto_row = ttk.Frame(scope, style='CardContent.TFrame')
        auto_row.grid(row=0, column=0, sticky='ew')
        auto_row.columnconfigure(0, weight=1)
        self.auto_row = auto_row
        auto_text = ttk.Frame(auto_row, style='CardContent.TFrame')
        auto_text.grid(row=0, column=0, sticky='w')
        ttk.Label(auto_text, text='自动同步', style='CardBody.TLabel').pack(anchor='w')
        ttk.Label(
            auto_text, text='检测到 WorkBuddy 账号切换后，自动把所选范围同步给新账号',
            style='MutedCard.TLabel',
        ).pack(anchor='w', pady=(3, 0))
        self.auto_button = ttk.Button(auto_row, command=self.toggle_auto, width=18)
        self.auto_button.grid(row=0, column=1, sticky='e', padx=(24, 0))

        self.scope_separator = ttk.Separator(scope, style='Card.TSeparator')
        self.scope_separator.grid(row=1, column=0, sticky='ew', pady=14)

        scope_row = ttk.Frame(scope, style='CardContent.TFrame')
        scope_row.grid(row=2, column=0, sticky='ew')
        scope_row.columnconfigure(0, weight=1)
        self.scope_row = scope_row
        scope_text = ttk.Frame(scope_row, style='CardContent.TFrame')
        scope_text.grid(row=0, column=0, sticky='w')
        ttk.Label(scope_text, text='同步范围', style='CardBody.TLabel').pack(anchor='w')
        ttk.Label(
            scope_text, text='默认同步全部普通会话，也可以改为手动选择',
            style='MutedCard.TLabel',
        ).pack(anchor='w', pady=(3, 0))
        self.all_scope_button = ttk.Button(
            scope_row, text='全部会话（推荐）', width=18,
            command=lambda: self.set_scope(True),
        )
        self.all_scope_button.grid(row=0, column=1, sticky='e', padx=(24, 0))
        self.selected_scope_button = ttk.Button(
            scope_row, text='仅选中的会话', width=18,
            command=lambda: self.set_scope(False),
        )
        self.selected_scope_button.grid(row=0, column=2, sticky='e', padx=(6, 0))

        scope_meta = ttk.Frame(scope, style='CardContent.TFrame')
        scope_meta.grid(row=3, column=0, sticky='ew', pady=(12, 0))
        scope_meta.columnconfigure(0, weight=1)
        self.scope_hint = tk.StringVar()
        ttk.Label(
            scope_meta, textvariable=self.scope_hint, style='MutedCard.TLabel',
        ).grid(row=0, column=0, sticky='w')
        self.selection_summary = tk.StringVar(value='全部 0')
        ttk.Label(
            scope_meta, textvariable=self.selection_summary, style='MutedCard.TLabel',
        ).grid(row=0, column=1, sticky='e', padx=(20, 0))

        bulk_actions = ttk.Frame(scope, style='CardContent.TFrame')
        bulk_actions.grid(row=4, column=0, sticky='w', pady=(10, 0))
        self.bulk_actions = bulk_actions
        self.select_all_button = ttk.Button(
            bulk_actions, text='全选列表', command=self.select_all_sessions,
            style='Secondary.TButton',
        )
        self.select_all_button.pack(side='left')
        self.clear_selection_button = ttk.Button(
            bulk_actions, text='清空选择', command=self.clear_selected_sessions,
            style='Secondary.TButton',
        )
        self.clear_selection_button.pack(side='left', padx=(6, 0))
        self._update_auto_control()

        table_card = self._card(page, row=3, column=0, sticky='nsew')
        table_card.columnconfigure(0, weight=1)
        table_card.rowconfigure(2, weight=1)
        ttk.Label(table_card, text='要接管的会话', style='AccountName.TLabel').grid(
            row=0, column=0, sticky='w')
        ttk.Label(
            table_card,
            text='只显示未归档的普通会话；在 WorkBuddy 归档后会自动移出，不再参与同步。',
            style='MutedCard.TLabel',
        ).grid(row=1, column=0, sticky='w', pady=(4, 10))
        self.tree = ttk.Treeview(
            table_card, columns=('selected', 'title', 'owner', 'id'), show='headings',
            selectmode='browse', height=8,
        )
        for key, title, width, stretch in [
            ('selected', '选择', 58, False), ('title', '会话标题', 560, True),
            ('owner', '当前所属账号', 210, False), ('id', '会话 ID', 150, False),
        ]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=50, stretch=stretch)
        self.tree.grid(row=2, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(table_card, orient='vertical', command=self.tree.yview)
        scrollbar.grid(row=2, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind('<Button-1>', self._on_tree_click, add='+')
        CellTooltip(self.tree, self._main_tree_tooltip)

        actions = ttk.Frame(page, style='App.TFrame')
        actions.grid(row=4, column=0, sticky='ew', pady=(12, 0))
        ttk.Label(
            actions,
            text=(
                '保存设置：保存同步规则　｜　同步一次：立即应用到当前账号　｜　'
                '刷新预览：只重新读取，不修改数据　｜　在 WorkBuddy 中打开：打开高亮的一条会话'
            ),
            style='Subtitle.TLabel',
        ).pack(anchor='w', pady=(0, 8))
        action_buttons = ttk.Frame(actions, style='App.TFrame')
        action_buttons.pack(anchor='w')
        self.save_button = ttk.Button(
            action_buttons, text='保存设置', command=self.save, style='Primary.TButton')
        self.save_button.pack(side='left')
        ttk.Button(
            action_buttons, text='同步一次', command=self.sync, style='Secondary.TButton',
        ).pack(
            side='left', padx=(8, 0))
        ttk.Button(action_buttons, text='刷新预览', command=self.refresh,
                   style='Secondary.TButton').pack(side='left', padx=(8, 0))
        ttk.Button(
            action_buttons, text='在 WorkBuddy 中打开', command=self.open_selected,
            style='Secondary.TButton',
        ).pack(side='left', padx=(8, 0))
        self.toggle_scope()

    def _build_history_page(self):
        page = self.history_page
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        note = tk.Frame(page, background='#F1F5F9', padx=14, pady=10)
        note.grid(row=0, column=0, sticky='ew', pady=(0, 12))
        tk.Label(
            note,
            text='账号名称来自 WorkBuddy 当前及历史登录快照；这里只读取账号名称和 ID，不展示登录凭据。',
            background='#F1F5F9', foreground=MUTED, anchor='w',
            font=(UI_FONT, 10),
        ).pack(fill='x')

        panes = ttk.Panedwindow(page, orient='horizontal')
        panes.grid(row=1, column=0, sticky='nsew')
        accounts_card = ttk.Frame(panes, style='Card.TFrame', padding=16)
        history_card = ttk.Frame(panes, style='Card.TFrame', padding=16)
        panes.add(accounts_card, weight=2)
        panes.add(history_card, weight=3)
        accounts_card.columnconfigure(0, weight=1)
        accounts_card.rowconfigure(1, weight=1)
        history_card.columnconfigure(0, weight=1)
        history_card.rowconfigure(2, weight=1)

        ttk.Label(accounts_card, text='已登录过的账号', style='AccountName.TLabel').grid(
            row=0, column=0, sticky='w', pady=(0, 10))
        self.account_tree = ttk.Treeview(
            accounts_card, columns=('name', 'current', 'count', 'id'), show='headings',
            selectmode='browse', height=14,
        )
        for key, title, width, stretch in [
            ('name', '账号名称', 190, True), ('current', '状态', 58, False),
            ('count', '会话', 54, False), ('id', '账号 ID', 125, False),
        ]:
            self.account_tree.heading(key, text=title)
            self.account_tree.column(key, width=width, minwidth=45, stretch=stretch)
        self.account_tree.grid(row=1, column=0, sticky='nsew')
        self.account_tree.bind('<<TreeviewSelect>>', self.show_account_history)
        CellTooltip(
            self.account_tree,
            lambda item, column: item if column == 'id' else '',
        )

        detail = ttk.Frame(history_card, style='Card.TFrame')
        detail.grid(row=0, column=0, sticky='ew')
        detail.columnconfigure(0, weight=1)
        self.history_account_name = tk.StringVar(value='请选择账号')
        self.history_account_id = tk.StringVar(value='')
        ttk.Label(
            detail, textvariable=self.history_account_name, style='AccountName.TLabel',
        ).grid(row=0, column=0, sticky='w')
        ttk.Button(
            detail, text='复制账号 ID', command=self.copy_account_id,
            style='Secondary.TButton',
        ).grid(row=0, column=1, rowspan=2, sticky='e')
        ttk.Label(detail, textvariable=self.history_account_id, style='Mono.TLabel').grid(
            row=1, column=0, sticky='w', pady=(4, 0))
        self.history_summary = tk.StringVar(value='当前归属的本地会话：0')
        ttk.Label(
            history_card, textvariable=self.history_summary, style='MutedCard.TLabel',
        ).grid(row=1, column=0, sticky='w', pady=(12, 8))

        self.history_tree = ttk.Treeview(
            history_card, columns=('title', 'id'), show='headings', selectmode='browse',
            height=13,
        )
        self.history_tree.heading('title', text='会话标题')
        self.history_tree.heading('id', text='会话 ID')
        self.history_tree.column('title', width=490, minwidth=220, stretch=True)
        self.history_tree.column('id', width=150, minwidth=120, stretch=False)
        self.history_tree.grid(row=2, column=0, sticky='nsew')
        CellTooltip(
            self.history_tree,
            lambda item, column: item if column == 'id' else '',
        )
        ttk.Button(
            history_card, text='在 WorkBuddy 中打开选中会话',
            command=self.open_history_selected, style='Secondary.TButton',
        ).grid(row=3, column=0, sticky='w', pady=(12, 0))

    def _build_settings_page(self):
        page = self.settings_page
        page.columnconfigure(0, weight=1)
        paths = self._card(page, row=0, column=0, sticky='ew')
        paths.columnconfigure(1, weight=1)
        ttk.Label(paths, text='数据位置', style='AccountName.TLabel').grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 12))
        self.data_path = tk.StringVar(value=str(self.cfg.data_dir))
        self.auth_path = tk.StringVar(value=str(self.cfg.auth_file))
        for row, (label, variable) in enumerate([
            ('WorkBuddy 数据目录', self.data_path), ('当前登录文件', self.auth_path)
        ], start=1):
            ttk.Label(paths, text=label, style='CardBody.TLabel').grid(
                row=row, column=0, sticky='w', padx=(0, 12), pady=5)
            ttk.Entry(paths, textvariable=variable).grid(row=row, column=1, sticky='ew', pady=5)
            ttk.Button(
                paths, text='选择', command=lambda target=row - 1: self.browse(target),
                style='Secondary.TButton',
            ).grid(row=row, column=2, padx=(8, 0), pady=5)
        ttk.Button(
            paths, text='保存路径设置', command=self.save, style='Primary.TButton',
        ).grid(row=3, column=0, columnspan=3, sticky='w', pady=(14, 0))

        recovery = self._card(page, row=1, column=0, sticky='ew', pady=(14, 0))
        recovery.columnconfigure(0, weight=1)
        ttk.Label(recovery, text='备份与恢复', style='AccountName.TLabel').grid(
            row=0, column=0, sticky='w')
        ttk.Label(
            recovery,
            text='恢复会改变会话的当前所属账号。操作前会再次备份，并要求 WorkBuddy 完全退出。',
            style='MutedCard.TLabel', wraplength=760,
        ).grid(row=1, column=0, columnspan=2, sticky='w', pady=(7, 14))
        ttk.Button(
            recovery, text='打开备份目录', command=self.open_backups,
            style='Secondary.TButton',
        ).grid(row=2, column=0, sticky='w')
        self.restore_button = ttk.Button(
            recovery, text='恢复归属…', command=self.restore, style='Warning.TButton')
        self.restore_button.grid(row=2, column=1, sticky='e', padx=(12, 0))

    def browse(self, target):
        path = filedialog.askdirectory(parent=self.root) if target == 0 else filedialog.askopenfilename(
            parent=self.root, title='选择 workbuddy-desktop-ai.info')
        if path:
            (self.data_path if target == 0 else self.auth_path).set(path)

    def save(self):
        try:
            self.cfg.data_dir = Path(self.data_path.get()).resolve()
            self.cfg.auth_file = Path(self.auth_path.get()).resolve()
            self.cfg.auto_sync = self.auto.get()
            self.cfg.session_ids = None if self.all_sessions.get() else sorted(
                self.selected_sessions)
            self.cfg.save(self.config_path)
            self.status.set('设置已保存。' + ('自动同步已开启。' if self.cfg.auto_sync else '自动同步已关闭。'))
        except (OSError, ValueError) as exc:
            self.show_error(exc)

    def refresh(self):
        try:
            highlighted = set(self.tree.selection())
            display_cfg = Settings(
                data_dir=self.cfg.data_dir, auth_file=self.cfg.auth_file,
                state_dir=self.cfg.state_dir,
            )
            display_engine = Engine(display_cfg)
            result = display_engine.preview()
            accounts = display_engine.accounts()
            self.accounts_by_id = {account.uid: account for account in accounts}
            names = {account.uid: account.name for account in accounts}
            current = next((account for account in accounts if account.current), None)
            if current is None and result.target:
                current = Account(result.target, '未命名账号', True)
                self.accounts_by_id[current.uid] = current
            self.current_account_full_id = current.uid if current else ''
            self.current_account_name.set(current.name if current else '未登录')
            self.current_account_id.set(current.uid if current else '—')

            existing = set(self.tree.get_children())
            self.session_owner_ids = {}
            for row in result.sessions:
                session_id = row['id']
                owner_id = row['user_id'] or ''
                self.session_owner_ids[session_id] = owner_id
                owner_name = names.get(owner_id)
                owner_display = (
                    f'{owner_name} · {short_id(owner_id)}' if owner_name else short_id(owner_id)
                )
                values = (
                    '☑' if session_id in self.selected_sessions else '☐',
                    display_title(row['title']), owner_display, short_id(session_id),
                )
                if self.tree.exists(session_id):
                    self.tree.item(session_id, values=values)
                    self.tree.move(session_id, '', 'end')
                else:
                    self.tree.insert('', 'end', iid=session_id, values=values)
            current_ids = {row['id'] for row in result.sessions}
            for item in existing - current_ids:
                self.tree.detach(item)
            keep_highlight = [item for item in highlighted if item in current_ids]
            if keep_highlight:
                self.tree.selection_set(keep_highlight)

            self.available_count.set(str(len(result.sessions)))
            self._update_scope_summary(len(result.sessions))
            self._refresh_account_tree(display_engine, accounts)
        except (SyncError, OSError, sqlite3.Error, ValueError) as exc:
            self.show_error(exc)

    def _refresh_account_tree(self, engine, accounts):
        selected = self.account_tree.selection()
        selected_id = selected[0] if selected else ''
        existing = set(self.account_tree.get_children())
        counts = {}
        for account in accounts:
            counts[account.uid] = len(engine.sessions_for_account(account.uid))
            values = (
                account.name, '当前' if account.current else '', counts[account.uid],
                short_id(account.uid),
            )
            if self.account_tree.exists(account.uid):
                self.account_tree.item(account.uid, values=values)
                self.account_tree.move(account.uid, '', 'end')
            else:
                self.account_tree.insert('', 'end', iid=account.uid, values=values)
        current_ids = {account.uid for account in accounts}
        for item in existing - current_ids:
            self.account_tree.detach(item)
        if selected_id not in current_ids:
            selected_id = next((account.uid for account in accounts if account.current), '')
        if not selected_id and accounts:
            selected_id = accounts[0].uid
        if selected_id:
            self.account_tree.selection_set(selected_id)
        self.show_account_history()

    def show_account_history(self, _event=None):
        selected = self.account_tree.selection()
        if not selected:
            self.history_account_name.set('请选择账号')
            self.history_account_id.set('')
            self.history_summary.set('当前归属的本地会话：0')
            return
        uid = selected[0]
        account = self.accounts_by_id.get(uid, Account(uid, '未命名账号'))
        self.history_account_name.set(account.name + ('（当前登录）' if account.current else ''))
        self.history_account_id.set(uid)
        rows = self.engine.sessions_for_account(uid)
        existing = set(self.history_tree.get_children())
        for row in rows:
            values = (display_title(row['title']), short_id(row['id']))
            if self.history_tree.exists(row['id']):
                self.history_tree.item(row['id'], values=values)
                self.history_tree.move(row['id'], '', 'end')
            else:
                self.history_tree.insert('', 'end', iid=row['id'], values=values)
        current_ids = {row['id'] for row in rows}
        for item in existing - current_ids:
            self.history_tree.detach(item)
        self.history_summary.set(f'当前归属的本地会话：{len(rows)}')

    def copy_account_id(self):
        value = self.history_account_id.get()
        if not value:
            self.status.set('请先选择一个账号。')
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.status.set('账号 ID 已复制。')

    def toggle_scope(self):
        columns = ('title', 'owner', 'id') if self.all_sessions.get() else (
            'selected', 'title', 'owner', 'id')
        self.tree.configure(displaycolumns=columns)
        is_all = self.all_sessions.get()
        self.all_scope_button.configure(
            style='SegmentSelected.TButton' if is_all else 'Segment.TButton')
        self.selected_scope_button.configure(
            style='Segment.TButton' if is_all else 'SegmentSelected.TButton')
        if is_all:
            self.bulk_actions.grid_remove()
            self.select_all_button.pack_forget()
            self.clear_selection_button.pack_forget()
        else:
            self.bulk_actions.grid()
            self.select_all_button.pack(side='left')
            self.clear_selection_button.pack(side='left', padx=(6, 0))
        self._update_scope_summary(len(self.tree.get_children()))

    def set_scope(self, all_sessions):
        self.all_sessions.set(all_sessions)
        self.toggle_scope()

    def toggle_auto(self):
        self.auto.set(not self.auto.get())
        self._update_auto_control()
        self.save()

    def _update_auto_control(self):
        enabled = self.auto.get()
        self.auto_button.configure(
            text='自动同步：开启' if enabled else '自动同步：关闭',
            style='ToggleOn.TButton' if enabled else 'ToggleOff.TButton',
        )

    def select_all_sessions(self):
        self.selected_sessions = set(self.tree.get_children())
        for item in self.tree.get_children():
            self.tree.set(item, 'selected', '☑')
        self._update_scope_summary(len(self.tree.get_children()))

    def clear_selected_sessions(self):
        self.selected_sessions.clear()
        for item in self.tree.get_children():
            self.tree.set(item, 'selected', '☐')
        self._update_scope_summary(len(self.tree.get_children()))

    def toggle_session(self, session_id):
        if self.all_sessions.get() or not self.tree.exists(session_id):
            return
        if session_id in self.selected_sessions:
            self.selected_sessions.remove(session_id)
        else:
            self.selected_sessions.add(session_id)
        self.tree.set(
            session_id, 'selected', '☑' if session_id in self.selected_sessions else '☐')
        self._update_scope_summary(len(self.tree.get_children()))

    def _update_scope_summary(self, total):
        if self.all_sessions.get():
            text = f'全部 {total}'
            shared = total
            hint = f'当前已包含全部 {total} 个未归档普通会话，无需手动选择。'
        else:
            shared = sum(item in self.selected_sessions for item in self.tree.get_children())
            text = f'已选 {shared} / {total}'
            hint = '点击表格左侧复选框选择会话，也可以全选或清空当前列表。'
        self.selection_summary.set(text)
        self.shared_count.set(str(shared))
        self.scope_hint.set(hint)

    def _on_tree_click(self, event):
        if self.all_sessions.get() or self.tree.identify_column(event.x) != '#1':
            return
        item = self.tree.identify_row(event.y)
        if item:
            self.toggle_session(item)
            return 'break'

    def _main_tree_tooltip(self, item, column):
        if column == 'id':
            return item
        if column == 'owner':
            return self.session_owner_ids.get(item, '')
        return ''

    def display_result(self, result):
        self.status.set(MESSAGES[result.status] + (
            f' 本次处理 {result.changed} 个会话。' if result.changed else ''))
        if result.backup:
            self.last_backup = result.backup
        if result.status in ('synced', 'restored'):
            self.refresh()

    def sync(self):
        self.save()
        try:
            self.display_result(self.engine.sync())
        except (SyncError, OSError, sqlite3.Error, ValueError) as exc:
            self.show_error(exc)

    def _selected_main_session(self):
        selected = self.tree.selection()
        if len(selected) == 1:
            return selected[0]
        if not self.all_sessions.get() and len(self.selected_sessions) == 1:
            return next(iter(self.selected_sessions))
        return ''

    def open_selected(self):
        session_id = self._selected_main_session()
        if not session_id:
            self.status.set('请在会话列表中只选择一个会话。')
            return
        os.startfile('workbuddy://chat/' + quote(session_id, safe=''))

    def open_history_selected(self):
        selected = self.history_tree.selection()
        if len(selected) != 1:
            self.status.set('请在账号历史中只选择一个会话。')
            return
        os.startfile('workbuddy://chat/' + quote(selected[0], safe=''))

    def poll(self):
        try:
            if self.cfg.auto_sync:
                self.display_result(self.engine.tick())
            self.refresh()
        except (SyncError, OSError, sqlite3.Error, ValueError) as exc:
            self.show_error(exc)
        self.root.after(self.cfg.poll_seconds * 1000, self.poll)

    def restore(self):
        folder = filedialog.askdirectory(
            parent=self.root, title='选择包含 operation.json 的备份目录',
            initialdir=str(self.cfg.state_dir / 'backups'))
        if not folder:
            return
        if not messagebox.askyesno(
            '确认恢复归属',
            '恢复会改变相关会话的当前所属账号。请确认 WorkBuddy 已完全退出，是否继续？',
            parent=self.root,
        ):
            return
        self.auto.set(False)
        self.save()
        try:
            self.display_result(self.engine.restore(Path(folder)))
        except (SyncError, OSError, sqlite3.Error, ValueError) as exc:
            self.show_error(exc)

    def open_backups(self):
        path = self.cfg.state_dir / 'backups'
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def show_error(self, exc):
        self.auto.set(False)
        self.cfg.auto_sync = False
        self.status.set('已暂停自动同步：' + str(exc))


def launch(config_path):
    enable_windows_dpi_awareness()
    root = tk.Tk()
    Window(root, config_path)
    root.mainloop()
