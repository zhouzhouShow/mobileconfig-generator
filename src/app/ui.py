# -*- coding: utf-8 -*-
"""中文界面：左侧配置项列表、中间表单、右侧 XML 实时预览。"""
from __future__ import annotations

import base64
import json
import os
import plistlib
import queue
import re
import sys
import tempfile
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from . import __version__
from . import profile as P
from . import signing
from . import update as UP
from . import upload as U

try:  # 拖拽支持：tkinterdnd2 不是标准库，缺失时程序仍能正常使用
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DND_AVAILABLE = True
    _TK_BASE = TkinterDnD.Tk
except Exception:  # noqa: BLE001
    DND_FILES = None
    DND_AVAILABLE = False
    _TK_BASE = tk.Tk

APP_TITLE = "Apple 配置描述文件生成器"
FONT = ("Microsoft YaHei UI", 10)
FONT_SM = ("Microsoft YaHei UI", 9)
FONT_MONO = ("Consolas", 9)
FONT_BRAND = ("Microsoft YaHei UI", 13, "bold")
FONT_TITLE = ("Microsoft YaHei UI", 15, "bold")
FONT_SECTION = ("Microsoft YaHei UI", 10, "bold")
FONT_EYEBROW = ("Microsoft YaHei UI", 8)

# 配色：浅色纸面 + 单一强调蓝，XML 预览用深色代码区形成层次
BG = "#F5F5F7"
SURFACE = "#FFFFFF"
SURFACE_ALT = "#F7F7F9"
INK = "#1D1D1F"
INK_SOFT = "#6E6E73"
INK_MUTED = "#8E8E93"
HAIRLINE = "#DCDCE1"
ACCENT = "#0071E3"
ACCENT_DARK = "#0058B0"
ACCENT_SOFT = "#E9F2FD"
SUCCESS = "#1D7A46"
WARNING = "#9A5B00"
DANGER = "#C0392B"
CODE_BG = "#1C1C1F"
CODE_FG = "#E4E4E8"
CODE_KEY = "#7CC4FF"
CODE_TAG = "#C792EA"
CODE_ERR = "#FF7A7A"


def app_data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "AppleMobileconfigGen")
    os.makedirs(path, exist_ok=True)
    return path


def settings_path() -> str:
    return os.path.join(app_data_dir(), "settings.json")


def load_settings() -> dict:
    try:
        with open(settings_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    try:
        with open(settings_path(), "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


def human_size(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "0 B"
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def parse_accept(accept: str):
    """把 "描述|*.a;*.b|描述2|*.*" 解析成 Tk 要求的 [(描述, "*.a *.b"), ...]。

    Tk 的 filetypes 只接受二元组，模式和描述必须成对出现；
    模式之间用分号分隔，最后合并成一个空格分隔的字符串。
    """
    parts = [x.strip() for x in (accept or "").split("|")]
    out = []
    for i in range(0, len(parts) - 1, 2):
        label, patterns = parts[i], parts[i + 1]
        patterns = " ".join(p.strip() for p in patterns.split(";") if p.strip())
        if label and patterns:
            out.append((label, patterns))
    return out or [("所有文件", "*.*")]


class MainWindow(_TK_BASE):
    def __init__(self, initial: str = ""):
        super().__init__()
        self.profile = P.Profile()
        self.current = "global"
        self.rows = []                 # [(field, container, parent_frame)]
        self.dirty = False
        self._preview_job = None
        self._issues = []
        self._dnd_map = {}

        self.title(APP_TITLE)
        # 窗口尺寸按屏幕可用区域收敛，避免在小屏 / 高缩放下大于桌面导致无法拖回
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        win_w, win_h = min(1440, max(1024, screen_w - 80)), min(880, max(680, screen_h - 140))
        pos_x = max(0, (screen_w - win_w) // 2)
        pos_y = max(0, (screen_h - win_h) // 3)
        self.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
        self.minsize(1024, 680)
        self.configure(bg=BG)

        self._init_style()
        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_status()
        self._bind_shortcuts()
        self._setup_drop()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_all()
        if initial:
            self.open_path(initial)
        self.after(2500, self._auto_check_update)
        # Win+D / 最小化后偶发无法恢复：窗口重新映射时校正位置并前置
        self._mapped_once = False
        self.bind("<Map>", self._on_window_map, add="+")

    # ------------------------------------------------------------- 窗口恢复
    def _on_window_map(self, event):
        if event.widget is not self:
            return
        if not self._mapped_once:
            self._mapped_once = True
            return
        if self.state() == "iconic":
            self.after(60, self._restore_window)
        else:
            self._restore_window()

    def _restore_window(self):
        try:
            self.deiconify()
            self._ensure_on_screen()
            for child in self.winfo_children():
                if isinstance(child, tk.Toplevel) and child.winfo_exists():
                    if not child.winfo_viewable():
                        child.deiconify()
                        child.lift()
            self.lift()
            self.attributes("-topmost", True)
            self.after(180, self._drop_topmost)
        except tk.TclError:
            pass

    def _drop_topmost(self):
        try:
            self.attributes("-topmost", False)
        except tk.TclError:
            pass

    def _ensure_on_screen(self):
        """窗口被恢复到屏幕外时，把标题栏移回可见区域。"""
        self.update_idletasks()
        if self.state() == "zoomed":
            return
        x, y = self.winfo_x(), self.winfo_y()
        width = self.winfo_width()
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        new_x = min(max(x, 200 - width), screen_w - 200)
        new_y = min(max(y, 0), screen_h - 120)
        if (new_x, new_y) != (x, y):
            self.geometry(f"+{new_x}+{new_y}")

    # ------------------------------------------------------------------ 拖拽
    def _setup_drop(self):
        """把窗口及其所有控件注册成文件拖放目标（Windows 下依赖 tkinterdnd2）。

        OLE 的落点判定以鼠标下的窗口为准，只注册根窗口时，拖到表单/预览等子控件上
        不会触发，所以这里整棵控件树都要注册，并在表单重建后补注册。
        """
        self.dnd_ready = False
        if not DND_AVAILABLE:
            return
        self._register_drop_tree()
        self.dnd_ready = str(self) in self._dnd_map
        if self.dnd_ready:
            self.status.configure(text="就绪 · 可把 .mobileconfig 拖进窗口直接打开")

    def _register_drop_tree(self):
        seen = {}
        stack = [self]
        while stack:
            w = stack.pop()
            try:
                key = str(w)
                if self._dnd_map.get(key) is not w:
                    w.drop_target_register(DND_FILES)
                    w.dnd_bind("<<Drop>>", self._on_drop)
                seen[key] = w
                stack.extend(w.winfo_children())
            except Exception:  # noqa: BLE001
                continue
        self._dnd_map = seen

    def _on_drop(self, event):
        """拖入文件：.mobileconfig 当描述文件、.json 当工程，与“打开/导入”等价。"""
        try:
            paths = [str(x) for x in self.tk.splitlist(event.data)]
        except Exception:  # noqa: BLE001
            paths = [x.strip("{}") for x in str(event.data).split()]
        files = [x for x in paths if x and os.path.isfile(x)]
        if not files:
            messagebox.showinfo(
                APP_TITLE, "请拖入一个 .mobileconfig 描述文件，或本工具保存的 .json 工程文件。",
                parent=self)
            return "break"
        path = files[0]
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".mobileconfig", ".json"):
            messagebox.showinfo(
                APP_TITLE,
                f"不支持的文件类型：{os.path.basename(path)}\n\n"
                "请拖入 .mobileconfig 描述文件，或本工具保存的 .json 工程文件。",
                parent=self)
            return "break"
        if not self.confirm_discard():
            return "break"
        self.open_path(path)
        self.status.configure(text=f"已加载 {os.path.basename(path)}", foreground=SUCCESS)
        return "copy"

    def open_path(self, path: str):
        """双击文件打开：.json 当工程，.mobileconfig 当描述文件。"""
        try:
            if path.lower().endswith(".json"):
                with open(path, "r", encoding="utf-8") as fh:
                    self.profile = P.Profile.from_json(json.load(fh))
            else:
                with open(path, "rb") as fh:
                    raw = fh.read()
                try:
                    self.profile, warns = P.parse_profile(raw)
                except ValueError:
                    inner = signing.extract_signed_content(raw)
                    if not inner:
                        raise
                    self.profile, warns = P.parse_profile(inner)
                if warns:
                    messagebox.showwarning(APP_TITLE, "\n".join(warns[:8]), parent=self)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"打开文件失败：{exc}", parent=self)
            return
        self.current = "global"
        self.dirty = False
        self.title(APP_TITLE + f" - {os.path.basename(path)}")
        self.refresh_all()

    # ------------------------------------------------------------------ 样式
    def _init_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")   # clam 才能完整控制颜色与边框
        except tk.TclError:
            pass

        style.configure(".", font=FONT, background=SURFACE, foreground=INK)
        style.configure("TFrame", background=SURFACE)
        style.configure("Page.TFrame", background=BG)
        style.configure("TLabel", background=SURFACE, foreground=INK, font=FONT)
        style.configure("Page.TLabel", background=BG, foreground=INK)
        style.configure("Brand.TLabel", background=SURFACE, foreground=INK, font=FONT_BRAND)
        style.configure("Head.TLabel", background=SURFACE, foreground=INK, font=FONT_TITLE)
        style.configure("Sub.TLabel", background=SURFACE, foreground=INK_SOFT, font=FONT_SM)
        style.configure("Eyebrow.TLabel", background=SURFACE, foreground=INK_MUTED,
                        font=FONT_EYEBROW)
        style.configure("Section.TLabel", background=SURFACE, foreground=INK,
                        font=FONT_SECTION)
        style.configure("Help.TLabel", background=SURFACE, foreground=INK_MUTED, font=FONT_SM)
        style.configure("Status.TLabel", background=SURFACE, foreground=INK_SOFT, font=FONT_SM)
        style.configure("Code.TLabel", background=BG, foreground=INK_SOFT, font=FONT_SM)

        # 按钮：默认幽灵按钮（白底细边），主操作用实心强调色
        # width=0 覆盖 clam 主题默认的 11 字符固定宽度，让按钮随文字自适应
        style.configure("TButton", font=FONT, padding=(14, 6), background=SURFACE,
                        foreground=INK, bordercolor="#C9C9CF", lightcolor="#C9C9CF",
                        darkcolor="#C9C9CF", focuscolor=ACCENT, relief="flat",
                        borderwidth=1, width=0)
        style.map("TButton",
                  background=[("pressed", "#E7E7EC"), ("active", "#F5F5F7")],
                  bordercolor=[("focus", ACCENT)],
                  foreground=[("disabled", INK_MUTED)])
        style.configure("Ghost.TButton", font=FONT, padding=(14, 6), background=SURFACE,
                        foreground=INK, bordercolor="#C9C9CF", lightcolor="#C9C9CF",
                        darkcolor="#C9C9CF", relief="flat", borderwidth=1, width=0)
        style.configure("Tool.TButton", font=FONT, padding=(14, 6), background=SURFACE,
                        foreground=INK, bordercolor="#C9C9CF", lightcolor="#C9C9CF",
                        darkcolor="#C9C9CF", relief="flat", borderwidth=1, width=0)
        style.configure("Small.TButton", font=FONT_SM, padding=(9, 4), background=SURFACE,
                        foreground=INK, bordercolor="#C9C9CF", lightcolor="#C9C9CF",
                        darkcolor="#C9C9CF", relief="flat", borderwidth=1, width=0)
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"),
                        padding=(18, 7), background=ACCENT, foreground="#FFFFFF",
                        bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
                        relief="flat", borderwidth=1, width=0)
        style.map("Primary.TButton",
                  background=[("pressed", ACCENT_DARK), ("active", "#0077ED")],
                  bordercolor=[("pressed", ACCENT_DARK), ("active", "#0077ED")],
                  foreground=[("disabled", "#F2F2F5")])
        for name in ("TMenubutton", "Ghost.TMenubutton"):
            style.configure(name, font=FONT, padding=(14, 6), background=SURFACE,
                            foreground=INK, bordercolor="#C9C9CF", lightcolor="#C9C9CF",
                            darkcolor="#C9C9CF", relief="flat", borderwidth=1,
                            arrowcolor=INK_SOFT, width=0)
            style.map(name,
                      background=[("pressed", "#E7E7EC"), ("active", "#F5F5F7")],
                      arrowcolor=[("active", ACCENT)])

        # 输入控件：浅灰内嵌底 + 聚焦描边
        style.configure("TEntry", padding=(8, 6), fieldbackground=SURFACE_ALT,
                        background=SURFACE_ALT, foreground=INK, bordercolor=HAIRLINE,
                        lightcolor=HAIRLINE, darkcolor=HAIRLINE, insertcolor=INK)
        style.map("TEntry",
                  bordercolor=[("focus", ACCENT)],
                  lightcolor=[("focus", ACCENT)],
                  darkcolor=[("focus", ACCENT)],
                  fieldbackground=[("readonly", SURFACE_ALT)],
                  foreground=[("disabled", INK_MUTED)])
        style.configure("TCombobox", padding=(8, 5), fieldbackground=SURFACE_ALT,
                        background=SURFACE_ALT, foreground=INK, bordercolor=HAIRLINE,
                        lightcolor=HAIRLINE, darkcolor=HAIRLINE, arrowcolor=INK_SOFT,
                        selectbackground=SURFACE_ALT, selectforeground=INK)
        style.map("TCombobox",
                  fieldbackground=[("readonly", SURFACE_ALT), ("disabled", SURFACE_ALT)],
                  bordercolor=[("focus", ACCENT), ("active", "#B9B9C0")],
                  arrowcolor=[("active", ACCENT)],
                  foreground=[("disabled", INK_MUTED)])
        self.option_add("*TCombobox*Listbox.background", SURFACE)
        self.option_add("*TCombobox*Listbox.foreground", INK)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT_SOFT)
        self.option_add("*TCombobox*Listbox.selectForeground", ACCENT_DARK)
        self.option_add("*TCombobox*Listbox.font", FONT)
        style.configure("TSpinbox", padding=(8, 5), fieldbackground=SURFACE_ALT,
                        background=SURFACE, foreground=INK, bordercolor=HAIRLINE,
                        lightcolor=HAIRLINE, darkcolor=HAIRLINE, arrowcolor=INK_SOFT)
        style.map("TSpinbox", bordercolor=[("focus", ACCENT)],
                  arrowcolor=[("active", ACCENT)])

        style.configure("TCheckbutton", background=SURFACE, foreground=INK, font=FONT,
                        focuscolor=SURFACE, indicatorcolor=SURFACE)
        style.map("TCheckbutton",
                  background=[("active", SURFACE)],
                  foreground=[("disabled", INK_MUTED)],
                  indicatorcolor=[("selected", ACCENT), ("active", ACCENT_SOFT)])

        style.configure("Treeview", font=FONT, rowheight=34, background=SURFACE,
                        fieldbackground=SURFACE, foreground=INK, borderwidth=0,
                        relief="flat")
        style.configure("Treeview.Heading", background=SURFACE, foreground=INK_SOFT,
                        font=FONT_SM, relief="flat", borderwidth=0)
        style.map("Treeview",
                  background=[("selected", ACCENT_SOFT)],
                  foreground=[("selected", ACCENT_DARK)])

        style.configure("TScrollbar", background="#C7C7CC", troughcolor=BG,
                        bordercolor=BG, arrowcolor=INK_SOFT, relief="flat",
                        arrowsize=12, gripcount=0, width=10)
        style.map("TScrollbar", background=[("active", "#AEAEB2")])
        style.configure("Dark.TScrollbar", background="#3A3A3F", troughcolor=CODE_BG,
                        bordercolor=CODE_BG, arrowcolor="#8E8E93", relief="flat",
                        arrowsize=12, gripcount=0, width=10)
        style.map("Dark.TScrollbar", background=[("active", "#55555C")])
        # 自定义滚动条需要显式继承布局，否则 Tk 会报 Layout ... not found
        for orient in ("Vertical", "Horizontal"):
            try:
                style.layout(f"{orient}.Dark.TScrollbar",
                             style.layout(f"{orient}.TScrollbar"))
            except tk.TclError:
                pass
        style.configure("TSeparator", background=HAIRLINE)
        style.configure("TPanedwindow", background=BG)

    def _bind_shortcuts(self):
        self.bind_all("<Control-n>", lambda e: self.new_profile())
        self.bind_all("<Control-o>", lambda e: self.open_project())
        self.bind_all("<Control-s>", lambda e: self.save_project())
        self.bind_all("<Control-i>", lambda e: self.import_mobileconfig())
        self.bind_all("<Control-e>", lambda e: self.export_unsigned())
        self.bind_all("<Control-S>", lambda e: self.open_sign_dialog())

    # ------------------------------------------------------------------ 菜单
    def _build_menu(self):
        menubar = tk.Menu(self)

        m_file = tk.Menu(menubar, tearoff=0, font=FONT)
        m_file.add_command(label="新建配置描述文件", accelerator="Ctrl+N", command=self.new_profile)
        m_file.add_command(label="打开工程…", accelerator="Ctrl+O", command=self.open_project)
        m_file.add_command(label="保存工程…", accelerator="Ctrl+S", command=self.save_project)
        m_file.add_separator()
        m_file.add_command(label="导入 .mobileconfig…", accelerator="Ctrl+I",
                           command=self.import_mobileconfig)
        m_file.add_command(label="导出未签名 .mobileconfig…", accelerator="Ctrl+E",
                           command=self.export_unsigned)
        m_file.add_command(label="签名并导出…", accelerator="Ctrl+Shift+S",
                           command=self.open_sign_dialog)
        m_file.add_separator()
        m_file.add_command(label="退出", command=self.on_close)
        menubar.add_cascade(label="文件", menu=m_file)

        m_tool = tk.Menu(menubar, tearoff=0, font=FONT)
        m_tool.add_command(label="查看证书信息 / 备案指纹…", command=self.open_certinfo)
        m_tool.add_command(label="上传 ipa 到 App Store Connect…", command=self.open_upload)
        m_tool.add_separator()
        m_tool.add_command(label="检测 OpenSSL 环境", command=self.detect_openssl)
        m_tool.add_command(label="设置 OpenSSL 路径…", command=self.pick_openssl)
        menubar.add_cascade(label="工具", menu=m_tool)

        m_help = tk.Menu(menubar, tearoff=0, font=FONT)
        m_help.add_command(label="使用说明", command=self.show_help)
        m_help.add_command(label="检查更新…", command=lambda: self.check_update(False))
        self.var_auto_update = tk.BooleanVar(
            value=bool(load_settings().get("check_update", True)))
        m_help.add_checkbutton(label="启动时自动检查更新", variable=self.var_auto_update,
                               command=self._toggle_auto_update)
        m_help.add_separator()
        m_help.add_command(label="关于", command=self.show_about)
        menubar.add_cascade(label="帮助", menu=m_help)

        self.config(menu=menubar)

    # ------------------------------------------------------------------ 工具栏
    def _build_toolbar(self):
        tk.Frame(self, bg=ACCENT, height=2).pack(side="top", fill="x")

        bar = tk.Frame(self, bg=SURFACE)
        bar.pack(side="top", fill="x")
        tk.Frame(self, bg=HAIRLINE, height=1).pack(side="top", fill="x")

        # 品牌区
        brand = tk.Frame(bar, bg=SURFACE)
        brand.pack(fill="x", padx=18, pady=(14, 2))
        tk.Label(brand, text="◈", bg=SURFACE, fg=ACCENT,
                 font=("Microsoft YaHei UI", 15, "bold")).pack(side="left", padx=(0, 9))
        name_box = tk.Frame(brand, bg=SURFACE)
        name_box.pack(side="left")
        tk.Label(name_box, text="描述文件生成器", bg=SURFACE, fg=INK,
                 font=FONT_BRAND).pack(anchor="w")
        tk.Label(name_box, text="Apple mobileconfig · 创建 · 签名 · 备案",
                 bg=SURFACE, fg=INK_MUTED, font=FONT_SM).pack(anchor="w")

        self.openssl_label = tk.Label(brand, text="", bg=SURFACE, fg=INK_SOFT,
                                      font=FONT_SM, padx=10, pady=4)
        self.openssl_label.pack(side="right")
        self._update_openssl_label()

        # 操作区
        actions = tk.Frame(bar, bg=SURFACE)
        actions.pack(fill="x", padx=18, pady=(8, 14))

        def divider():
            tk.Frame(actions, bg=HAIRLINE, width=1).pack(side="left", fill="y",
                                                         padx=10, pady=1)

        for label, cmd in (("新建", self.new_profile),
                           ("打开工程", self.open_project),
                           ("保存工程", self.save_project)):
            ttk.Button(actions, text=label, style="Ghost.TButton",
                       command=cmd).pack(side="left", padx=(0, 8))
        divider()
        for label, cmd in (("导入 .mobileconfig", self.import_mobileconfig),
                           ("导出 .mobileconfig", self.export_unsigned),
                           ("证书信息", self.open_certinfo),
                           ("上传 ipa", self.open_upload)):
            ttk.Button(actions, text=label, style="Ghost.TButton",
                       command=cmd).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="签名并导出", style="Primary.TButton",
                   command=self.open_sign_dialog).pack(side="right")

    def _update_openssl_label(self):
        path = load_settings().get("openssl_path", "")
        try:
            exe = signing.find_openssl(path)
            ver = signing.openssl_version(exe)
            text = "● OpenSSL 已就绪" + (f" · {ver.split(' ')[1]}" if ver else "")
            self.openssl_label.configure(text=text, foreground=SUCCESS,
                                         background="#EDF7F1")
        except signing.OpenSSLNotFound:
            self.openssl_label.configure(text="● OpenSSL 未找到 · 仍可导出未签名",
                                         foreground=WARNING, background="#FFF6E8")

    # ------------------------------------------------------------------ 主体
    def _build_body(self):
        body = tk.Frame(self, bg=BG)
        body.pack(side="top", fill="both", expand=True, padx=16, pady=14)

        outer = ttk.PanedWindow(body, orient="horizontal")
        outer.pack(fill="both", expand=True)

        # 左：配置项列表（白卡片）
        left = tk.Frame(outer, bg=SURFACE, highlightbackground=HAIRLINE,
                        highlightthickness=1, bd=0, width=262)
        left.pack_propagate(False)
        outer.add(left, weight=0)

        left_head = tk.Frame(left, bg=SURFACE)
        left_head.pack(fill="x", padx=14, pady=(14, 8))
        tk.Label(left_head, text="配置项", bg=SURFACE, fg=INK,
                 font=FONT_SECTION).pack(side="left")
        self.tree_count = tk.Label(left_head, text="", bg=SURFACE, fg=INK_MUTED,
                                   font=FONT_SM)
        self.tree_count.pack(side="right")

        tree_box = tk.Frame(left, bg=SURFACE)
        tree_box.pack(fill="both", expand=True, padx=(6, 2))
        self.tree = ttk.Treeview(tree_box, show="tree", selectmode="browse")
        tsb = ttk.Scrollbar(tree_box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.tag_configure("global", foreground=ACCENT_DARK)
        self.tree.tag_configure("item", foreground=INK)

        btns = tk.Frame(left, bg=SURFACE)
        btns.pack(fill="x", padx=10, pady=(6, 12))

        self.add_btn = ttk.Menubutton(btns, text="＋ 添加配置项", style="Ghost.TMenubutton")
        add_menu = tk.Menu(self.add_btn, tearoff=0, font=FONT, bg=SURFACE, fg=INK,
                           activebackground=ACCENT_SOFT, activeforeground=ACCENT_DARK,
                           bd=0)
        for spec in P.ADDABLE:
            add_menu.add_command(label=f"{spec.label}", command=lambda k=spec.key: self.add_payload(k))
        self.add_btn["menu"] = add_menu
        self.add_btn.pack(fill="x")

        order_row = tk.Frame(btns, bg=SURFACE)
        order_row.pack(fill="x", pady=(6, 0))
        ttk.Button(order_row, text="删除", style="Small.TButton",
                   command=self.delete_payload).pack(side="left")
        ttk.Button(order_row, text="↑ 上移", style="Small.TButton",
                   command=lambda: self.move_payload(-1)).pack(side="left", padx=(6, 0))
        ttk.Button(order_row, text="↓ 下移", style="Small.TButton",
                   command=lambda: self.move_payload(1)).pack(side="left", padx=(6, 0))

        # 右：表单 + 预览
        right = ttk.PanedWindow(outer, orient="horizontal")
        outer.add(right, weight=5)

        form_wrap = tk.Frame(right, bg=BG)
        right.add(form_wrap, weight=4)
        form_head = tk.Frame(form_wrap, bg=BG)
        form_head.pack(fill="x", padx=2, pady=(0, 6))
        tk.Label(form_head, text="详细设置", bg=BG, fg=INK,
                 font=FONT_SECTION).pack(side="left")
        tk.Label(form_head, text="修改会实时反映到右侧 XML", bg=BG, fg=INK_MUTED,
                 font=FONT_SM).pack(side="left", padx=(10, 0))

        self.form_canvas = tk.Canvas(form_wrap, bg=SURFACE, highlightthickness=1,
                                     highlightbackground=HAIRLINE, bd=0, width=620)
        fsb = ttk.Scrollbar(form_wrap, orient="vertical", command=self.form_canvas.yview)
        self.form_canvas.configure(yscrollcommand=fsb.set)
        self.form_canvas.pack(side="left", fill="both", expand=True)
        fsb.pack(side="right", fill="y")
        self.form_body = tk.Frame(self.form_canvas, bg=SURFACE)
        self._form_window = self.form_canvas.create_window((0, 0), window=self.form_body, anchor="nw")
        self.form_body.bind("<Configure>",
                            lambda e: self.form_canvas.configure(scrollregion=self.form_canvas.bbox("all")))
        self.form_canvas.bind("<Configure>",
                             lambda e: self.form_canvas.itemconfigure(self._form_window, width=e.width))
        self.form_canvas.bind("<Enter>", lambda e: self._bind_wheel(True))
        self.form_canvas.bind("<Leave>", lambda e: self._bind_wheel(False))

        preview_wrap = tk.Frame(right, bg=BG)
        right.add(preview_wrap, weight=5)
        head = tk.Frame(preview_wrap, bg=BG)
        head.pack(side="top", fill="x", padx=2, pady=(0, 6))
        tk.Label(head, text="XML 预览", bg=BG, fg=INK,
                 font=FONT_SECTION).pack(side="left")
        self.preview_info = tk.Label(head, text="", bg=BG, fg=INK_MUTED, font=FONT_SM)
        self.preview_info.pack(side="right")

        issues_card = tk.Frame(preview_wrap, bg=SURFACE, highlightbackground=HAIRLINE,
                               highlightthickness=1, bd=0)
        issues_card.pack(side="bottom", fill="x", pady=(10, 0))
        tk.Label(issues_card, text="检查结果", bg=SURFACE, fg=INK_MUTED,
                 font=FONT_SM, anchor="w").pack(fill="x", padx=12, pady=(8, 2))
        self.issues_text = tk.Text(issues_card, height=4, font=FONT_SM, relief="flat",
                                   bg=SURFACE, fg=INK, padx=10, pady=2, wrap="word",
                                   bd=0, cursor="arrow")
        self.issues_text.pack(fill="x", padx=2, pady=(0, 8))
        self.issues_text.tag_configure("error", foreground=DANGER)
        self.issues_text.tag_configure("warn", foreground=WARNING)
        self.issues_text.tag_configure("ok", foreground=SUCCESS)
        self.issues_text.configure(state="disabled")

        pbody = tk.Frame(preview_wrap, bg=CODE_BG)
        pbody.pack(side="top", fill="both", expand=True)
        self.preview = tk.Text(pbody, wrap="none", font=FONT_MONO, bg=CODE_BG,
                               foreground=CODE_FG, relief="flat", padx=12, pady=10,
                               undo=False, width=44, bd=0, insertbackground=CODE_FG,
                               selectbackground="#3A3A3F", selectforeground="#FFFFFF")
        psb = ttk.Scrollbar(pbody, orient="vertical", command=self.preview.yview,
                            style="Dark.TScrollbar")
        hsb = ttk.Scrollbar(pbody, orient="horizontal", command=self.preview.xview,
                            style="Dark.TScrollbar")
        self.preview.configure(yscrollcommand=psb.set, xscrollcommand=hsb.set)
        psb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self.preview.pack(side="left", fill="both", expand=True)
        self.preview.tag_configure("key", foreground=CODE_KEY)
        self.preview.tag_configure("tag", foreground=CODE_TAG)
        self.preview.tag_configure("err", foreground=CODE_ERR)
        self.preview.configure(state="disabled")

    def _bind_wheel(self, on: bool):
        if on:
            self.form_canvas.bind_all("<MouseWheel>", self._on_form_wheel)
        else:
            self.form_canvas.unbind_all("<MouseWheel>")

    def _on_form_wheel(self, event):
        self.form_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _build_status(self):
        tk.Frame(self, bg=HAIRLINE, height=1).pack(side="bottom", fill="x")
        bar = tk.Frame(self, bg=SURFACE)
        bar.pack(side="bottom", fill="x")
        self.status = tk.Label(bar, text="就绪", bg=SURFACE, fg=INK_SOFT,
                               font=FONT_SM, anchor="w")
        self.status.pack(side="left", padx=16, pady=7)
        tk.Label(bar, text="Ctrl+S 保存 · Ctrl+Shift+S 签名", bg=SURFACE,
                 fg=INK_MUTED, font=FONT_SM).pack(side="right", padx=16)

    # ------------------------------------------------------------------ 列表
    def refresh_tree(self, select: str = None):
        self.tree.delete(*self.tree.get_children())
        self.tree.insert("", "end", iid="global", text="  ⚙   全局设置", tags=("global",))
        for p in self.profile.payloads:
            self.tree.insert("", "end", iid=p.uid, text=f"  •   {p.summary()}", tags=("item",))
        if getattr(self, "tree_count", None) is not None:
            self.tree_count.configure(text=f"{len(self.profile.payloads)} 项")
        target = select or self.current
        if target != "global" and target not in {p.uid for p in self.profile.payloads}:
            target = "global"
        self.tree.selection_set(target)
        self.tree.see(target)

    def selected_payload(self):
        for p in self.profile.payloads:
            if p.uid == self.current:
                return p
        return None

    def _on_tree_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        if sel[0] != self.current:
            self.current = sel[0]
            self.render_form()

    def add_payload(self, spec_key: str):
        p = self.profile.add_payload(spec_key)
        if spec_key in ("rootcert", "pkcs12"):
            p.display_name = "根证书" if spec_key == "rootcert" else "客户端证书"
        self.current = p.uid
        self.mark_dirty()
        self.refresh_tree(p.uid)
        self.render_form()
        self.schedule_preview()

    def delete_payload(self):
        p = self.selected_payload()
        if p is None:
            messagebox.showinfo(APP_TITLE, "请先在左侧选择要删除的配置项。", parent=self)
            return
        if not messagebox.askyesno(APP_TITLE, f"确定删除「{p.summary()}」吗？", parent=self):
            return
        self.profile.payloads.remove(p)
        self.current = "global"
        self.mark_dirty()
        self.refresh_tree("global")
        self.render_form()
        self.schedule_preview()

    def move_payload(self, delta: int):
        p = self.selected_payload()
        if p is None:
            messagebox.showinfo(APP_TITLE, "请先选择一个配置项。", parent=self)
            return
        items = self.profile.payloads
        idx = items.index(p)
        new = idx + delta
        if 0 <= new < len(items):
            items[idx], items[new] = items[new], items[idx]
            self.mark_dirty()
            self.refresh_tree(p.uid)
            self.schedule_preview()

    def mark_dirty(self):
        self.dirty = True
        self.title(APP_TITLE + " *")

    # ------------------------------------------------------------------ 表单
    def render_form(self):
        for child in self.form_body.winfo_children():
            child.destroy()
        self.rows = []
        self.form_canvas.yview_moveto(0)

        header = tk.Frame(self.form_body, bg=SURFACE)
        header.pack(fill="x", padx=18, pady=(20, 2))
        if self.current == "global":
            title = "全局设置"
            sub = "这些字段会写入描述文件顶层，作用于整份配置"
        else:
            p = self.selected_payload()
            if p is None:
                self.current = "global"
                return self.render_form()
            title = p.type_label()
            sub = "PayloadType：" + (p.spec.payload_type if p.spec else
                                     (p.raw or {}).get("PayloadType", ""))
        tk.Label(header, text=title, bg=SURFACE, fg=INK,
                 font=FONT_TITLE).pack(anchor="w")
        tk.Label(header, text=sub, bg=SURFACE, fg=INK_SOFT,
                 font=FONT_SM).pack(anchor="w", pady=(3, 0))
        tk.Frame(self.form_body, bg=HAIRLINE, height=1).pack(fill="x", padx=18, pady=(12, 0))

        if self.current == "global":
            self.render_global()
        else:
            self.render_payload(self.selected_payload())
        if getattr(self, "dnd_ready", False):
            self._register_drop_tree()

    # -- 全局 ------------------------------------------------------------
    def render_global(self):
        meta = self.profile.meta
        fields = [
            ("display_name", "配置名称", "text", True, "安装时显示在设置里的名称"),
            ("identifier", "标识符", "text", True, "默认 com.组织.profile.随机串，可自行修改"),
            ("organization", "组织名称", "text", False, "公司或学校名称，会写入每个配置项"),
            ("description", "描述", "multiline", False, ""),
            ("expiration", "到期时间", "date", False, "格式 2027-12-31T00:00:00Z，留空表示不过期"),
            ("consent", "同意文本", "multiline", False, "ConsentText：安装前展示给用户的说明，留空则不显示"),
        ]
        grp = self._group("基本信息")
        for key, label, kind, req, help_text in fields:
            if kind == "multiline":
                self._text_row(grp, meta, key, label, help_text)
            elif kind == "date":
                self._entry_row(grp, meta, key, label, help_text, req)
            else:
                self._entry_row(grp, meta, key, label, help_text, req)

        advanced = self._row(grp)
        var = tk.BooleanVar(value=bool(meta.get("removal_disallowed")))
        cb = ttk.Checkbutton(advanced, text="禁止移除该描述文件（PayloadRemovalDisallowed）",
                             variable=var, command=lambda: self._set(meta, "removal_disallowed", var.get()))
        cb.pack(side="left")

        tools = self._row(grp)
        ttk.Button(tools, text="按组织重新生成标识符",
                   command=self.regen_profile_identifier).pack(side="left")
        ttk.Button(tools, text="重新生成配置 UUID",
                   command=self.regen_profile_uuid).pack(side="left", padx=6)

    def regen_profile_identifier(self):
        meta = self.profile.meta
        slug = self.profile.org_slug()
        meta["identifier"] = f"com.{slug}.profile.{P.uuid_short(meta['uuid']).lower()}"
        self.mark_dirty()
        self.render_form()
        self.schedule_preview()

    def regen_profile_uuid(self):
        meta = self.profile.meta
        old = meta["uuid"]
        meta["uuid"] = P.new_uuid()
        self.profile.replace_uuid_refs(old, meta["uuid"])
        self.mark_dirty()
        self.render_form()
        self.schedule_preview()

    # -- 单个 payload -----------------------------------------------------
    def render_payload(self, p):
        if p is None:
            return
        if p.is_raw:
            self._group("原样保留的配置项")
            box = ttk.Frame(self.form_body)
            box.pack(fill="both", expand=True, padx=14, pady=8)
            ttk.Label(box, text="该配置项类型本工具暂不支持编辑，导出时会原样写回，不会丢失。",
                      style="Help.TLabel", wraplength=520).pack(anchor="w")
            raw_xml = plistlib.dumps(p.raw or {}, fmt=plistlib.FMT_XML, sort_keys=False).decode("utf-8")
            txt = tk.Text(box, height=18, font=FONT_MONO, wrap="char", bg=CODE_BG,
                          fg=CODE_FG, relief="flat", bd=0, padx=10, pady=8,
                          insertbackground=CODE_FG)
            txt.insert("1.0", raw_xml)
            txt.configure(state="disabled")
            txt.pack(fill="both", expand=True, pady=(8, 0))
            return

        grp = self._group("配置项信息")
        self._payload_meta_rows(grp, p)

        fields = p.spec.fields
        if not fields:
            return
        current_group = None
        frame = None
        for fld in fields:
            if fld.group != current_group:
                current_group = fld.group
                frame = self._group(current_group)
            self._render_field(frame, fld, p.data)
        self.apply_visibility()

    def _payload_meta_rows(self, parent, p):
        self._entry_row(parent, p, "display_name", "显示名称", "安装界面中显示的标题", True)
        self._entry_row(parent, p, "identifier", "标识符", "默认 com.组织.类型.随机串，可修改", True)
        self._entry_row(parent, p, "organization", "组织", "留空则使用全局组织名称", False)

        uuid_row = self._row(parent)
        ttk.Label(uuid_row, text="UUID", anchor="e").grid(row=0, column=0, sticky="e", padx=(0, 8))
        uuid_box = ttk.Frame(uuid_row)
        uuid_box.grid(row=0, column=1, sticky="ew")
        var = tk.StringVar(value=p.uuid)
        entry = ttk.Entry(uuid_box, textvariable=var, state="readonly")
        # 没有 trace 的 StringVar 会被垃圾回收，导致显示为空，这里保留引用
        entry._tk_var = var
        entry.pack(side="left", fill="x", expand=True)
        ttk.Button(uuid_box, text="重新生成", width=9,
                   command=lambda: self.regen_payload_uuid(p)).pack(side="left", padx=(6, 0))

        self._text_row(parent, p, "description", "描述", "可选")

    def regen_payload_uuid(self, p):
        old = p.uuid
        p.uuid = P.new_uuid()
        p.uid = p.uuid
        self.profile.replace_uuid_refs(old, p.uuid)
        self.current = p.uid
        self.mark_dirty()
        self.refresh_tree(p.uid)
        self.render_form()
        self.schedule_preview()

    # -- 通用控件 ---------------------------------------------------------
    def _group(self, title: str):
        card = tk.Frame(self.form_body, bg=SURFACE, highlightbackground=HAIRLINE,
                        highlightthickness=1, bd=0)
        card.pack(fill="x", padx=18, pady=(14, 0))
        cap = tk.Frame(card, bg=SURFACE)
        cap.pack(fill="x", padx=14, pady=(12, 2))
        tk.Frame(cap, bg=ACCENT, width=16, height=2).pack(side="left", padx=(0, 8))
        tk.Label(cap, text=title, bg=SURFACE, fg=INK,
                 font=FONT_SECTION).pack(side="left")
        holder = tk.Frame(card, bg=SURFACE)
        holder.pack(fill="x", padx=10, pady=(2, 12))
        holder.columnconfigure(0, minsize=150)
        holder.columnconfigure(1, weight=1)
        return holder

    def _row(self, parent):
        idx = parent.grid_size()[1]
        outer = ttk.Frame(parent)
        outer.grid(row=idx, column=0, columnspan=2, sticky="ew", padx=6, pady=5)
        outer.columnconfigure(0, minsize=150)
        outer.columnconfigure(1, weight=1, minsize=220)
        return outer

    def _set(self, target, key: str, value):
        if self._get(target, key) == value:
            return
        if isinstance(target, dict):
            target[key] = value
        else:
            setattr(target, key, value)
        self.mark_dirty()
        self.schedule_preview()

    @staticmethod
    def _get(target, key: str):
        if isinstance(target, dict):
            return target.get(key, "")
        return getattr(target, key, "")

    def _entry_row(self, parent, target, key, label, help_text="", required=False):
        outer = self._row(parent)
        text = ("* " if required else "") + label
        ttk.Label(outer, text=text, anchor="e").grid(row=0, column=0, sticky="e", padx=(0, 8))
        box = ttk.Frame(outer)
        box.grid(row=0, column=1, sticky="ew")
        var = tk.StringVar(value=str(self._get(target, key) or ""))
        entry = ttk.Entry(box, textvariable=var)
        entry.pack(fill="x")
        if help_text:
            ttk.Label(box, text=help_text, style="Help.TLabel").pack(anchor="w")
        var.trace_add("write", lambda *_: self._set(target, key, var.get()))
        return entry

    def _text_row(self, parent, target, key, label, help_text=""):
        outer = self._row(parent)
        ttk.Label(outer, text=label, anchor="ne").grid(row=0, column=0, sticky="ne", padx=(0, 8))
        box = ttk.Frame(outer)
        box.grid(row=0, column=1, sticky="ew")
        txt = tk.Text(box, height=4, font=FONT, wrap="word", relief="flat", bd=0,
                      bg=SURFACE_ALT, fg=INK, highlightthickness=1,
                      highlightbackground=HAIRLINE, highlightcolor=ACCENT,
                      insertbackground=INK, padx=8, pady=6)
        txt.insert("1.0", str(self._get(target, key) or ""))
        txt.pack(fill="x")

        def on_modify(_event=None):
            if not txt.edit_modified():
                return
            self._set(target, key, txt.get("1.0", "end-1c"))
            txt.edit_modified(False)

        txt.bind("<<Modified>>", on_modify)
        if help_text:
            ttk.Label(box, text=help_text, style="Help.TLabel").pack(anchor="w")
        return txt

    def _render_field(self, parent, fld, data: dict):
        outer = self._row(parent)
        self.rows.append((fld, outer, parent, data))

        if fld.kind == "bool":
            var = tk.BooleanVar(value=bool(data.get(fld.key, fld.default)))
            text = fld.label + (f"（{fld.help}）" if fld.help else "")
            cb = ttk.Checkbutton(outer, text=text, variable=var,
                                 command=lambda: self._set(data, fld.key, var.get()))
            cb.grid(row=0, column=0, columnspan=2, sticky="w")
            return

        label = ("* " if fld.required else "") + fld.label
        ttk.Label(outer, text=label, anchor="e").grid(row=0, column=0, sticky="e", padx=(0, 8))
        box = ttk.Frame(outer)
        box.grid(row=0, column=1, sticky="ew")

        if fld.kind in ("text", "password"):
            var = tk.StringVar(value=str(data.get(fld.key, "") or ""))
            entry = ttk.Entry(box, textvariable=var, show="*" if fld.kind == "password" else "")
            entry.pack(fill="x")
            var.trace_add("write", lambda *_: self._set(data, fld.key, var.get()))
            self._help(box, fld.help)

        elif fld.kind == "int":
            var = tk.StringVar(value="" if data.get(fld.key, "") in ("", None)
                               else str(data.get(fld.key)))
            spin = ttk.Spinbox(box, textvariable=var, from_=0, to=999999, width=12)
            spin.pack(anchor="w")

            def on_int(*_):
                raw = var.get().strip()
                try:
                    value = int(raw) if raw else ""
                except ValueError:
                    value = ""
                self._set(data, fld.key, value)

            var.trace_add("write", on_int)
            self._help(box, fld.help)

        elif fld.kind == "date":
            row = ttk.Frame(box)
            row.pack(fill="x")
            var = tk.StringVar(value=str(data.get(fld.key, "") or ""))
            ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="一年后", width=8,
                       command=lambda: var.set(P.iso_now_plus(365))).pack(side="left", padx=(4, 0))
            var.trace_add("write", lambda *_: self._set(data, fld.key, var.get()))
            self._help(box, fld.help)

        elif fld.kind == "choice":
            labels = [label for _value, label in fld.options]
            values = [value for value, _label in fld.options]
            cur = data.get(fld.key, fld.default)
            if cur not in values:
                cur = fld.default if fld.default in values else (values[0] if values else "")
            var = tk.StringVar(value=dict(fld.options).get(cur, labels[0] if labels else ""))
            combo = ttk.Combobox(box, textvariable=var, values=labels,
                                 state="readonly", width=34)
            combo.pack(anchor="w")

            def on_choice(_e=None):
                idx = labels.index(var.get()) if var.get() in labels else 0
                self._set(data, fld.key, values[idx] if values else "")
                self.apply_visibility()

            combo.bind("<<ComboboxSelected>>", on_choice)
            self._help(box, fld.help)

        elif fld.kind == "file":
            row = ttk.Frame(box)
            row.pack(fill="x")
            blob = data.get(fld.key) or {}
            name = blob.get("filename") if isinstance(blob, dict) else ""
            desc = f"{name}（{human_size(blob.get('size', 0))}）" if name else "尚未选择文件"
            lbl = ttk.Label(row, text=desc, style="Help.TLabel" if not name else "TLabel")
            lbl.pack(side="left")
            ttk.Button(row, text="选择…", width=7,
                       command=lambda: self._pick_file(fld, data, lbl)).pack(side="left", padx=(8, 0))
            if name:
                ttk.Button(row, text="清除", width=6,
                           command=lambda: self._clear_file(fld, data)).pack(side="left", padx=4)
            self._help(box, fld.help)

        elif fld.kind == "ref":
            candidates = [x for x in self.profile.payloads
                          if x.type_key == "pkcs12" and x.uid != self.current]
            labels = ["（未选择）"] + [f"{x.display_name} · {P.uuid_short(x.uuid)}" for x in candidates]
            uuids = [""] + [x.uuid for x in candidates]
            cur = data.get(fld.key, "")
            var = tk.StringVar(value=labels[uuids.index(cur)] if cur in uuids else labels[0])
            combo = ttk.Combobox(box, textvariable=var, values=labels, state="readonly", width=38)
            combo.pack(anchor="w")

            def on_ref(_e=None):
                idx = labels.index(var.get()) if var.get() in labels else 0
                self._set(data, fld.key, uuids[idx])

            combo.bind("<<ComboboxSelected>>", on_ref)
            self._help(box, fld.help)

    def _help(self, parent, text):
        if text:
            ttk.Label(parent, text=text, style="Help.TLabel", wraplength=430,
                      justify="left").pack(anchor="w")

    def _pick_file(self, fld, data, lbl):
        try:
            path = filedialog.askopenfilename(
                parent=self, title="选择文件", filetypes=parse_accept(fld.accept))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"打开文件选择框失败：{exc}", parent=self)
            return
        if not path:
            return
        try:
            if fld.key == "cert":
                raw = P.load_certificate_der(path)
                data[fld.key] = {
                    "filename": os.path.basename(path).rsplit(".", 1)[0] + ".cer",
                    "b64": base64.b64encode(raw).decode("ascii"),
                    "size": len(raw),
                    "kind": "cert",
                }
            else:
                data[fld.key] = P.make_blob(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"读取文件失败：{exc}", parent=self)
            return
        blob = data[fld.key]
        name = blob.get("filename", "")
        lbl.configure(text=f"{name}（{human_size(blob.get('size', 0))}）",
                      style="TLabel")
        self.mark_dirty()
        self.render_form()
        self.schedule_preview()

    def _clear_file(self, fld, data):
        data[fld.key] = {}
        self.mark_dirty()
        self.render_form()
        self.schedule_preview()

    def apply_visibility(self):
        groups = {}
        for fld, outer, parent, data in self.rows:
            if fld.show_if is None:
                continue
            try:
                visible = bool(fld.show_if(data))
            except Exception:
                visible = True
            shown = outer.winfo_manager() == "grid"
            if visible and not shown:
                outer.grid()
            elif not visible and shown:
                outer.grid_remove()
            groups.setdefault(parent, []).append(outer)

        # 整组都被隐藏时，把这一组的边框也收起来
        for parent, widgets in groups.items():
            frame = parent.master
            any_visible = any(w.winfo_manager() == "grid" for w in widgets)
            shown = bool(frame.winfo_manager())
            if any_visible and not shown:
                frame.pack(fill="x", padx=18, pady=(14, 0))
            elif not any_visible and shown:
                frame.pack_forget()

    # ------------------------------------------------------------------ 预览
    def schedule_preview(self):
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(220, self.refresh_preview)

    def refresh_preview(self):
        self._preview_job = None
        try:
            raw = P.dump_xml(self.profile)
        except Exception as exc:  # noqa: BLE001
            self._set_preview(f"<!-- 生成失败：{exc} -->")
            self.preview_info.configure(text="生成失败")
            return
        text = raw.decode("utf-8")
        self._set_preview(text)
        self.preview_info.configure(text=f"{len(raw)} 字节 · XML plist")

        self._issues = P.validate(self.profile)
        errors, warns = P.issues_summary(self._issues)
        self.issues_text.configure(state="normal")
        self.issues_text.delete("1.0", "end")
        if not self._issues:
            self.issues_text.insert("end", "✓ 检查通过，可以直接导出。\n", "ok")
        for issue in errors:
            self.issues_text.insert("end", f"✗ {issue.message}\n", "error")
        for issue in warns:
            self.issues_text.insert("end", f"! {issue.message}\n", "warn")
        self.issues_text.configure(state="disabled")

        n = len(self.profile.payloads)
        if errors:
            self.status.configure(
                text=f"共 {n} 个配置项 · 错误 {len(errors)} 个 · 警告 {len(warns)} 个 —— 建议修正后再导出",
                foreground=DANGER)
        elif warns:
            self.status.configure(
                text=f"共 {n} 个配置项 · 警告 {len(warns)} 个 —— 可以导出",
                foreground=WARNING)
        else:
            self.status.configure(text=f"共 {n} 个配置项 · 检查通过，可以导出",
                                  foreground=SUCCESS)

    def _set_preview(self, text: str):
        t = self.preview
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("1.0", text)
        pos = "1.0"
        while True:
            hit = t.search(r"<key>[^<]*</key>", pos, stopindex="end", regexp=True)
            if not hit:
                break
            end = f"{hit}+{len(t.get(hit, hit + ' lineend'))}c"
            t.tag_add("key", hit, end)
            pos = end
        pos = "1.0"
        while True:
            hit = t.search(r"</?(string|integer|real|true|false|data|date|array|dict|plist)>",
                           pos, stopindex="end", regexp=True)
            if not hit:
                break
            end = f"{hit}+{len(t.get(hit, hit + ' lineend'))}c"
            t.tag_add("tag", hit, end)
            pos = end
        t.configure(state="disabled")

    def refresh_all(self):
        self.refresh_tree(self.current)
        self.render_form()
        self.refresh_preview()

    # ------------------------------------------------------------------ 文件
    def new_profile(self):
        if not self.confirm_discard():
            return
        self.profile = P.Profile()
        self.current = "global"
        self.dirty = False
        self.title(APP_TITLE)
        self.refresh_all()

    def confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        ans = messagebox.askyesnocancel(
            APP_TITLE, "当前工程有未保存的修改，是否先保存？", parent=self)
        if ans is None:
            return False
        if ans:
            return self.save_project()
        return True

    def open_project(self):
        if not self.confirm_discard():
            return
        path = filedialog.askopenfilename(
            parent=self, title="打开工程",
            filetypes=[("工程文件", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
            if obj.get("format") != "apple-mobileconfig-generator/project":
                if not messagebox.askyesno(APP_TITLE, "该文件不是本工具的工程文件，仍要尝试打开吗？",
                                           parent=self):
                    return
            self.profile = P.Profile.from_json(obj)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"打开工程失败：{exc}", parent=self)
            return
        self.current = "global"
        self.dirty = False
        self.title(APP_TITLE + f" - {os.path.basename(path)}")
        self.refresh_all()

    def save_project(self) -> bool:
        default = re.sub(r"[\\/:*?\"<>|]+", "_", self.profile.meta.get("display_name") or "profile")
        path = filedialog.asksaveasfilename(
            parent=self, title="保存工程", defaultextension=".json",
            initialfile=default + ".json",
            filetypes=[("工程文件", "*.json")])
        if not path:
            return False
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.profile.to_json(), fh, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"保存失败：{exc}", parent=self)
            return False
        self.dirty = False
        self.title(APP_TITLE + f" - {os.path.basename(path)}")
        self.status.configure(text=f"工程已保存到 {path}", foreground=SUCCESS)
        return True

    def import_mobileconfig(self):
        if not self.confirm_discard():
            return
        path = filedialog.askopenfilename(
            parent=self, title="导入 .mobileconfig",
            filetypes=[("配置描述文件", "*.mobileconfig"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            try:
                prof, warnings = P.parse_profile(raw)
            except ValueError:
                inner = signing.extract_signed_content(raw)
                if not inner:
                    raise
                prof, warnings = P.parse_profile(inner)
                warnings.insert(0, "该文件带签名，已提取其中的内容用于编辑；签名不会保留，导出后需重新签名")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"导入失败：{exc}", parent=self)
            return
        self.profile = prof
        self.current = "global"
        self.dirty = False
        self.title(APP_TITLE + f" - {os.path.basename(path)}")
        self.refresh_all()
        msg = f"已导入 {len(prof.payloads)} 个配置项。"
        if warnings:
            msg += "\n\n注意：\n" + "\n".join(f"· {w}" for w in warnings[:8])
        messagebox.showinfo(APP_TITLE, msg, parent=self)

    def _default_name(self) -> str:
        name = self.profile.meta.get("display_name") or "profile"
        return re.sub(r"[\\/:*?\"<>|]+", "_", name)

    def export_unsigned(self):
        errors, _warns = P.issues_summary(P.validate(self.profile))
        if errors:
            listing = "\n".join(f"· {i.message}" for i in errors[:6])
            if not messagebox.askyesno(
                    APP_TITLE,
                    f"还有 {len(errors)} 个错误：\n\n{listing}\n\n仍要导出吗？", parent=self):
                return
        path = filedialog.asksaveasfilename(
            parent=self, title="导出 .mobileconfig", defaultextension=".mobileconfig",
            initialfile=self._default_name() + ".mobileconfig",
            filetypes=[("配置描述文件", "*.mobileconfig")])
        if not path:
            return
        try:
            with open(path, "wb") as fh:
                fh.write(P.dump_xml(self.profile))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"导出失败：{exc}", parent=self)
            return
        messagebox.showinfo(
            APP_TITLE,
            f"已导出未签名描述文件：\n{path}\n\n"
            "安装方式：用 Safari 或“文件”App 打开该文件，系统会提示安装。",
            parent=self)
        self.status.configure(text=f"已导出 {path}", foreground=SUCCESS)

    # ------------------------------------------------------------------ 签名
    def open_sign_dialog(self):
        SignDialog(self)

    def open_certinfo(self):
        CertInfoDialog(self)

    def open_upload(self):
        UploadDialog(self)

    # ------------------------------------------------------------------ 更新
    def _toggle_auto_update(self):
        data = load_settings()
        data["check_update"] = bool(self.var_auto_update.get())
        save_settings(data)

    def _auto_check_update(self):
        if self.var_auto_update.get():
            self.check_update(True)

    def check_update(self, silent: bool):
        if getattr(self, "_update_checking", False):
            return
        self._update_checking = True
        self._update_result = None

        def work():
            try:
                self._update_result = (UP.check(__version__), "", silent)
            except UP.UpdateError as exc:
                self._update_result = (None, str(exc), silent)

        threading.Thread(target=work, daemon=True).start()
        self._poll_update()

    def _poll_update(self):
        if self._update_result is None:
            self.after(200, self._poll_update)
            return
        info, error, silent = self._update_result
        self._update_result = None
        self._update_checking = False
        self._show_update_result(info, error, silent)

    def _show_update_result(self, info, error, silent):
        if info:
            UpdateDialog(self, info)
            return
        if silent:
            return
        if error:
            messagebox.showwarning("检查更新", f"检查更新失败：\n{error}", parent=self)
        else:
            messagebox.showinfo("检查更新", f"当前已是最新版本（{__version__}）。", parent=self)

    def detect_openssl(self):
        preferred = load_settings().get("openssl_path", "")
        try:
            exe = signing.find_openssl(preferred)
        except signing.OpenSSLNotFound as exc:
            messagebox.showwarning(APP_TITLE, str(exc), parent=self)
            return
        ver = signing.openssl_version(exe)
        messagebox.showinfo(APP_TITLE, f"已找到 OpenSSL：\n{exe}\n\n版本：{ver}", parent=self)

    def pick_openssl(self):
        path = filedialog.askopenfilename(
            parent=self, title="选择 openssl 可执行文件",
            filetypes=[("openssl", "openssl.exe"), ("所有文件", "*.*")])
        if not path:
            return
        cfg = load_settings()
        cfg["openssl_path"] = path
        save_settings(cfg)
        self._update_openssl_label()
        messagebox.showinfo(APP_TITLE, f"已记录 OpenSSL 路径：\n{path}", parent=self)

    # ------------------------------------------------------------------ 帮助
    def show_help(self):
        HelpDialog(self)

    def show_about(self):
        messagebox.showinfo(
            APP_TITLE,
            f"{APP_TITLE}  v{__version__}\n\n"
            "在 Windows 上创建、编辑、导出并签名 Apple 配置描述文件（.mobileconfig），"
            "定位等同于 Apple Configurator 的“编辑配置描述文件”功能。\n\n"
            "技术栈：Python 3 + Tkinter（标准库 plistlib 生成 plist），"
            "签名调用本机 OpenSSL smime。",
            parent=self)

    def on_close(self):
        if not self.confirm_discard():
            return
        self.destroy()


class SignDialog(tk.Toplevel):
    def __init__(self, app: MainWindow):
        super().__init__(app)
        self.app = app
        self.title("签名并导出")
        self.geometry("660x556")
        self.resizable(False, False)
        self.transient(app)
        self.grab_set()
        self.configure(bg=SURFACE)

        cfg = load_settings()
        self.var_cert = tk.StringVar()
        self.var_key = tk.StringVar()
        self.var_chain = tk.StringVar()
        self.var_pass = tk.StringVar()
        self.var_openssl = tk.StringVar(value=cfg.get("openssl_path", ""))
        self.var_out = tk.StringVar(value="")

        pad = {"padx": 18, "pady": 5}
        tk.Frame(self, bg=ACCENT, height=2).pack(fill="x")
        ttk.Label(self, text="签名并导出",
                  font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w", padx=18, pady=(16, 2))
        ttk.Label(self, text="使用 OpenSSL 做 S/MIME 签名（SHA-256，DER 输出）",
                  style="Sub.TLabel").pack(anchor="w", padx=18, pady=(0, 2))
        ttk.Label(self, text="iOS 只在描述文件带有效签名时才显示“已验证”。",
                  style="Help.TLabel").pack(anchor="w", padx=18, pady=(0, 10))
        tk.Frame(self, bg=HAIRLINE, height=1).pack(fill="x", padx=18, pady=(0, 8))

        self._file_row("签名证书 *", self.var_cert,
                       [("证书/P12", "*.pem;*.crt;*.cer;*.p12;*.pfx"), ("所有文件", "*.*")])
        self._file_row("私钥文件", self.var_key,
                       [("私钥", "*.key;*.pem"), ("所有文件", "*.*")])
        self._file_row("中间证书链", self.var_chain,
                       [("证书链", "*.pem;*.crt;*.cer"), ("所有文件", "*.*")])

        row = ttk.Frame(self)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="密码", width=14, anchor="e").pack(side="left", padx=(0, 8))
        ttk.Entry(row, textvariable=self.var_pass, show="*").pack(side="left", fill="x", expand=True)
        ttk.Label(self, text="证书是 .p12/.pfx 时这里填 P12 密码，会自动分离证书与私钥；私钥有密码时也填这里。",
                  style="Help.TLabel").pack(anchor="w", padx=14)

        self._file_row("OpenSSL", self.var_openssl,
                       [("openssl", "openssl.exe"), ("所有文件", "*.*")], save_key="openssl_path")
        self.openssl_status = ttk.Label(self, text="", style="Help.TLabel")
        self.openssl_status.pack(anchor="w", padx=14, pady=(0, 6))
        self._check_openssl()

        row = ttk.Frame(self)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="输出文件", width=14, anchor="e").pack(side="left", padx=(0, 8))
        ttk.Entry(row, textvariable=self.var_out).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="另存为…", command=self._choose_out).pack(side="left", padx=(6, 0))

        ttk.Label(self, text="留空则保存到工程所在目录，文件名与配置名称一致。",
                  style="Help.TLabel").pack(anchor="w", padx=14)

        btns = ttk.Frame(self)
        btns.pack(fill="x", side="bottom", pady=16, padx=18)
        ttk.Button(btns, text="开始签名并导出", style="Primary.TButton",
                   command=self.do_sign).pack(side="right")
        ttk.Button(btns, text="取消", style="Ghost.TButton",
                   command=self.destroy).pack(side="right", padx=8)
        ttk.Button(btns, text="仅导出未签名", style="Ghost.TButton",
                   command=self.export_plain).pack(side="left")

    def _file_row(self, label, var, filetypes, save_key=""):
        row = ttk.Frame(self)
        row.pack(fill="x", padx=14, pady=4)
        ttk.Label(row, text=label, width=14, anchor="e").pack(side="left", padx=(0, 8))
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)

        def browse():
            path = filedialog.askopenfilename(parent=self, filetypes=filetypes)
            if path:
                var.set(path)
                if save_key:
                    cfg = load_settings()
                    cfg[save_key] = path
                    save_settings(cfg)
                    self._check_openssl()

        ttk.Button(row, text="浏览…", width=7, command=browse).pack(side="left", padx=(6, 0))

    def _check_openssl(self):
        try:
            exe = signing.find_openssl(self.var_openssl.get())
            ver = signing.openssl_version(exe)
            self.openssl_status.configure(text=f"✓ {exe}  {ver}", foreground=SUCCESS)
            return exe
        except signing.OpenSSLNotFound:
            self.openssl_status.configure(
                text="✗ 未找到 OpenSSL：可以点“仅导出未签名”，或先安装 OpenSSL / 手动指定路径",
                foreground=DANGER)
            return None

    def _choose_out(self):
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".mobileconfig",
            initialfile=self.app._default_name() + ".mobileconfig",
            filetypes=[("配置描述文件", "*.mobileconfig")])
        if path:
            self.var_out.set(path)

    def export_plain(self):
        self.destroy()
        self.app.export_unsigned()

    def do_sign(self):
        exe = self._check_openssl()
        if not exe:
            if messagebox.askyesno(
                    APP_TITLE,
                    "没有找到 OpenSSL，无法签名。\n\n是否改为导出未签名的描述文件？",
                    parent=self):
                self.export_plain()
            return
        if not self.var_cert.get().strip():
            messagebox.showwarning(APP_TITLE, "请选择签名证书。", parent=self)
            return
        out = self.var_out.get().strip()
        if not out:
            out = os.path.join(os.getcwd(), self.app._default_name() + ".mobileconfig")
        if not out.lower().endswith(".mobileconfig"):
            out += ".mobileconfig"

        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="mcunsigned_")
        tmp_src = os.path.join(tmpdir, "unsigned.mobileconfig")
        try:
            with open(tmp_src, "wb") as fh:
                fh.write(P.dump_xml(self.app.profile))
            self.app.status.configure(text="正在签名…", foreground=ACCENT_DARK)
            self.update_idletasks()
            result = signing.sign_file(
                exe, tmp_src, out,
                cert_path=self.var_cert.get().strip(),
                key_path=self.var_key.get().strip(),
                chain_path=self.var_chain.get().strip(),
                cert_password=self.var_pass.get(),
                key_password=self.var_pass.get())
        except signing.OpenSSLNotFound as exc:
            messagebox.showerror(APP_TITLE, str(exc), parent=self)
            return
        except signing.SignError as exc:
            detail = ("\n\n--- OpenSSL 输出 ---\n" + exc.log) if exc.log else ""
            messagebox.showerror(APP_TITLE, f"签名失败：{exc}{detail}", parent=self)
            return
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"签名失败：{exc}", parent=self)
            return

        verify_text = "签名自检：通过" if result.get("verified") else "签名自检：未通过（请检查私钥是否匹配证书）"
        messagebox.showinfo(
            APP_TITLE,
            f"已生成签名后的描述文件：\n{out}\n\n"
            f"大小：{human_size(result.get('size', 0))}（DER 编码）\n{verify_text}",
            parent=self)
        self.app.status.configure(text=f"已签名导出 {out}", foreground=SUCCESS)
        self.destroy()


class CertInfoDialog(tk.Toplevel):
    """读取 Android / Apple 证书，提取 APP 备案需要的指纹与公钥。"""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.results = []
        self.title("证书信息 / 备案指纹")
        self.geometry("800x680")
        self.minsize(700, 560)
        self.transient(app)
        self.configure(bg=SURFACE)

        self.var_path = tk.StringVar()
        self.var_pwd = tk.StringVar()

        tk.Frame(self, bg=ACCENT, height=2).pack(fill="x")
        ttk.Label(self, text="证书信息 / 备案指纹",
                  font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w", padx=18, pady=(16, 2))
        ttk.Label(self,
                  text="选择 Android（.keystore / .jks）或 Apple（.p12 / .pfx / .cer）证书，"
                       "读取备案常用的 MD5、SHA-1、SHA-256 指纹与公钥。",
                  style="Sub.TLabel", wraplength=750, justify="left").pack(
                      anchor="w", padx=18, pady=(0, 4))
        ttk.Label(self,
                  text="Android 一般填 SHA-1 / MD5 指纹 + 公钥；iOS 一般填公钥 + Bundle ID。",
                  style="Help.TLabel", wraplength=750, justify="left").pack(
                      anchor="w", padx=18, pady=(0, 10))
        tk.Frame(self, bg=HAIRLINE, height=1).pack(fill="x", padx=18, pady=(0, 6))

        row = ttk.Frame(self)
        row.pack(fill="x", padx=18, pady=5)
        ttk.Label(row, text="证书文件", width=10, anchor="e").pack(side="left", padx=(0, 8))
        ttk.Entry(row, textvariable=self.var_path).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="浏览…", width=7, style="Ghost.TButton",
                   command=self._browse).pack(side="left", padx=(6, 0))

        row = ttk.Frame(self)
        row.pack(fill="x", padx=18, pady=5)
        ttk.Label(row, text="密码", width=10, anchor="e").pack(side="left", padx=(0, 8))
        ttk.Entry(row, textvariable=self.var_pwd, show="*").pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="读取证书信息", style="Primary.TButton",
                   command=self._load).pack(side="left", padx=(8, 0))

        self.status = ttk.Label(self, text="", style="Help.TLabel")
        self.status.pack(anchor="w", padx=18, pady=(2, 6))

        box = tk.Frame(self, bg=SURFACE, highlightbackground=HAIRLINE,
                       highlightthickness=1, bd=0)
        box.pack(fill="both", expand=True, padx=18)
        self.text = tk.Text(box, wrap="word", font=FONT_MONO, bg=SURFACE_ALT,
                            fg=INK, relief="flat", bd=0, padx=10, pady=8,
                            insertbackground=INK, selectbackground=ACCENT_SOFT,
                            selectforeground=ACCENT_DARK)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=18, pady=12)
        for label, key in [("复制全文", "all"), ("复制 SHA-1", "sha1"),
                           ("复制 MD5", "md5"), ("复制 SHA-256", "sha256"),
                           ("复制公钥", "public_key_b64")]:
            ttk.Button(btns, text=label, style="Small.TButton",
                       command=lambda k=key: self._copy(k)).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="关闭", style="Ghost.TButton",
                   command=self.destroy).pack(side="right")

    def _browse(self):
        path = filedialog.askopenfilename(
            parent=self, title="选择证书文件",
            filetypes=[("证书文件", "*.p12;*.pfx;*.keystore;*.jks;*.cer;*.crt;*.der;*.pem"),
                       ("所有文件", "*.*")])
        if path:
            self.var_path.set(path)

    def _load(self):
        path = self.var_path.get().strip()
        if not path:
            messagebox.showwarning(APP_TITLE, "请先选择证书文件。", parent=self)
            return
        self.status.configure(text="正在读取…", foreground=ACCENT_DARK)
        self.update_idletasks()
        try:
            from . import certinfo
            results, note = certinfo.read_certificates(path, self.var_pwd.get())
        except Exception as exc:  # noqa: BLE001
            self.status.configure(text="读取失败", foreground=DANGER)
            messagebox.showerror(APP_TITLE, f"读取证书失败：\n{exc}", parent=self)
            return
        self.results = results
        tip = f"（{note}）" if note else ""
        self.status.configure(text=f"已读取 {len(results)} 张证书{tip}", foreground=SUCCESS)
        self._show(results)

    def _show(self, results):
        lines = []
        for idx, info in enumerate(results, 1):
            title = "签名证书" if idx == 1 else f"证书链第 {idx} 张"
            lines.append(f"===== {title} =====")
            lines.append(f"主题 Subject：{info.get('subject', '')}")
            lines.append(f"颁发者 Issuer：{info.get('issuer', '')}")
            lines.append(f"序列号 Serial：{info.get('serial', '')}")
            lines.append(f"生效时间：{info.get('not_before', '')}")
            lines.append(f"失效时间：{info.get('not_after', '')}")
            lines.append(f"MD5 指纹：{info.get('md5', '')}")
            lines.append(f"SHA-1 指纹：{info.get('sha1', '')}")
            lines.append(f"SHA-256 指纹：{info.get('sha256', '')}")
            alg = info.get("key_alg", "")
            bits = info.get("key_bits", "")
            lines.append(f"公钥算法：{alg}{(' ' + bits + ' 位') if bits else ''}")
            lines.append(f"签名算法：{info.get('sig_alg', '')}")
            lines.append(f"公钥（Base64）：{info.get('public_key_b64', '')}")
            lines.append(f"证书（Base64 DER）：{info.get('cert_b64', '')}")
            lines.append("")
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "\n".join(lines))
        self.text.configure(state="disabled")

    def _copy(self, key: str):
        if not self.results:
            messagebox.showinfo(APP_TITLE, "请先读取证书。", parent=self)
            return
        if key == "all":
            payload = self.text.get("1.0", "end-1c")
        else:
            payload = "\n".join(str(i.get(key, "")) for i in self.results if i.get(key))
        if not payload.strip():
            return
        self.clipboard_clear()
        self.clipboard_append(payload)
        self.status.configure(text="已复制到剪贴板", foreground=SUCCESS)


class HelpDialog(tk.Toplevel):
    TEXT = (
        "一、新建与编辑\n"
        "1. 打开程序后默认进入“全局设置”，填写配置名称、组织、描述等。\n"
        "2. 点左侧“＋ 添加配置项”，选择 Wi-Fi、根证书、PKCS#12、Web Clip、访问限制。\n"
        "3. 选中配置项后在中间填写参数，右侧会实时刷新 XML。\n"
        "4. 每个配置项的 UUID 自动生成；标识符默认为 com.组织.类型.随机串，可手动修改。\n\n"
        "二、企业 Wi-Fi（802.1X）\n"
        "1. 先添加“PKCS#12 证书”，选择 .p12 并填入 P12 密码。\n"
        "2. 再添加 Wi-Fi，安全类型选“企业级 802.1X”，EAP 类型选 EAP-TLS，"
        "在“客户端证书”里选中刚添加的 PKCS#12 配置项。\n"
        "3. 使用 PEAP/TTLS 时填用户名与密码即可，不需要证书。\n\n"
        "三、导入已有描述文件\n"
        "“导入 .mobileconfig”会解析本工具支持的 5 类配置项；不认识的类型会原样保留，"
        "导出时不会丢失。签名过的描述文件也能导入（签名不会保留，需要重新签）。\n"
        "也可以把 .mobileconfig 文件直接拖到窗口上打开（拖入其他类型的文件会给出提示）。\n\n"
        "四、导出与签名\n"
        "1. “导出 .mobileconfig”输出 UTF-8 的 XML plist。\n"
        "2. “签名并导出”调用 openssl smime -sign（SHA-256、DER、-nodetach）。"
        "证书可以是 .pem/.crt，也可以是 .p12（需要密码）。\n"
        "3. 找不到 OpenSSL 时会提示，并可选择导出未签名版本。\n\n"
        "五、安装到 iPhone / iPad\n"
        "1. 直接把 .mobileconfig 放到网页上，用 Safari 下载；HTTP 响应头必须是 "
        "application/x-apple-aspen-config。\n"
        "2. 也可以用“文件”App、邮件附件、AirDrop 打开，然后再到"
        "“设置 → 通用 → VPN 与设备管理”里安装。\n"
        "3. 安装根证书后，还需到“设置 → 通用 → 关于本机 → 证书信任设置”里"
        "手动开启对该根证书的完全信任。\n"
        "4. 未签名的描述文件会显示“未验证”，仍然可以安装；"
        "带 PayloadRemovalDisallowed=true 的描述文件只能通过抹掉设备移除。\n\n"
        "六、工程文件\n"
        "“保存工程”把当前内容存成 .json（含证书二进制内容），"
        "下次“打开工程”即可继续编辑。\n"
        "\n"
        "七、上传 ipa 到 App Store Connect\n"
        "1. 工具栏点“上传 ipa”，首次使用先点“下载”安装 Apple 官方 Windows 版\n"
        "   Transporter 4.3（免费，自带 Java）；已装在别的盘时点“选择目录…”指定。\n"
        "2. 认证有两种方式：\n"
        "   · API Key（推荐）：App Store Connect → 用户和访问 → 集成 → 生成密钥，\n"
        "     记下 Key ID、Issuer ID，并下载 .p8（只能下载一次）。\n"
        "   · Apple ID 账号密码：填 Apple ID 和应用专用密码（在 appleid.apple.com\n"
        "     生成，不是登录密码）；填好后可点“获取团队”，登录并直接选团队。\n"
        "3. 选择 ipa，填好认证信息，点“开始上传”。\n"
        "4. 上传只是把包交给 Apple，构建处理完成后还需在 App Store Connect\n"
        "   里填写版本信息并提交审核。\n"
        "5. 若自动上传报错，可点“复制命令”到命令提示符手动执行，查看完整日志。"
    )

    def __init__(self, app):
        super().__init__(app)
        self.title("使用说明")
        self.geometry("780x640")
        self.transient(app)
        self.configure(bg=SURFACE)
        tk.Frame(self, bg=ACCENT, height=2).pack(fill="x")
        tk.Label(self, text="使用说明", bg=SURFACE, fg=INK,
                 font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w", padx=18, pady=(14, 8))
        frame = tk.Frame(self, bg=SURFACE, highlightbackground=HAIRLINE,
                         highlightthickness=1, bd=0)
        frame.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        txt = tk.Text(frame, wrap="word", font=FONT, relief="flat", bd=0,
                      bg=SURFACE_ALT, fg=INK, padx=14, pady=12,
                      insertbackground=INK, spacing1=2, spacing3=4)
        txt.insert("1.0", self.TEXT)
        txt.configure(state="disabled")
        sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")


class UploadDialog(tk.Toplevel):
    """通过 Apple 官方 iTMSTransporter 把 ipa 上传到 App Store Connect。"""

    KIND_TEAM = "团队密钥（AuthKey）"
    KIND_SELF = "个人密钥（ApiKey）"
    AUTH_API_LABEL = "API Key（推荐）"
    AUTH_ACCOUNT_LABEL = "Apple ID 账号密码"

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("上传 ipa 到 App Store Connect")
        self.geometry("820x790")
        self.minsize(760, 660)
        self.transient(app)
        self.grab_set()
        self.configure(bg=SURFACE)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._queue = queue.Queue()
        self._team_queue = queue.Queue()
        self._teams_loading = False
        self._cancel = threading.Event()
        self._worker = None
        self._log_tail = []

        saved = load_settings()
        self.var_ipa = tk.StringVar()
        self.var_key_id = tk.StringVar(value=saved.get("asc_key_id", ""))
        self.var_issuer = tk.StringVar(value=saved.get("asc_issuer", ""))
        self.var_key = tk.StringVar(value=saved.get("asc_key_path", ""))
        self.var_exe = tk.StringVar(value=saved.get("itms_path", ""))
        self.var_kind = tk.StringVar(
            value=self.KIND_SELF if saved.get("asc_key_type") == "individual" else self.KIND_TEAM)
        self.var_auth = tk.StringVar(
            value=self.AUTH_ACCOUNT_LABEL
            if saved.get("asc_auth", U.AUTH_ACCOUNT) == U.AUTH_ACCOUNT
            else self.AUTH_API_LABEL)
        self.var_apple_id = tk.StringVar(value=saved.get("asc_apple_id", ""))
        saved_password = ""
        try:
            if saved.get("asc_password"):
                saved_password = base64.b64decode(
                    saved["asc_password"].encode("ascii")).decode("utf-8")
        except Exception:  # noqa: BLE001
            saved_password = ""
        self.var_password = tk.StringVar(value=saved_password)
        self.var_team = tk.StringVar(value=saved.get("asc_team_id", ""))

        tk.Frame(self, bg=ACCENT, height=2).pack(fill="x")
        ttk.Label(self, text="上传 ipa 到 App Store Connect",
                  font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w", padx=18, pady=(16, 2))
        ttk.Label(self, text="使用 Apple 官方免费的 Windows 版 iTMSTransporter 上传，不需要 Mac。"
                             "支持 API Key 或 Apple ID 登录。",
                  style="Sub.TLabel", wraplength=780).pack(anchor="w", padx=18)
        link_row = tk.Frame(self, bg=SURFACE)
        link_row.pack(anchor="w", padx=18, pady=(4, 8))
        self._link(link_row, "获取 API Key ↗",
                   "https://appstoreconnect.apple.com/access/integrations/api").pack(side="left")
        self._link(link_row, "生成应用专用密码 ↗",
                   "https://appleid.apple.com/account/manage").pack(side="left", padx=(14, 0))
        tk.Frame(self, bg=HAIRLINE, height=1).pack(fill="x", padx=18, pady=(0, 8))

        form = tk.Frame(self, bg=SURFACE)
        form.pack(fill="x", padx=18)
        form.columnconfigure(1, weight=1)

        def row_label(parent, text, row):
            tk.Label(parent, text=text, bg=SURFACE, fg=INK_SOFT, font=FONT_SM,
                     anchor="e").grid(row=row, column=0, sticky="e", padx=(0, 10), pady=5)

        def browse_box(parent, row, var, kinds, title):
            box = tk.Frame(parent, bg=SURFACE)
            box.grid(row=row, column=1, sticky="ew", pady=5)
            ttk.Entry(box, textvariable=var).pack(side="left", fill="x", expand=True)

            def pick():
                path = filedialog.askopenfilename(title=title, filetypes=kinds)
                if path:
                    var.set(path)
                    if var is self.var_ipa:
                        self._load_ipa(path)

            ttk.Button(box, text="浏览…", width=6, style="Ghost.TButton",
                       command=pick).pack(side="left", padx=(6, 0))
            return box

        row_label(form, "IPA 文件", 0)
        browse_box(form, 0, self.var_ipa, [("iOS 应用包", "*.ipa"), ("所有文件", "*.*")],
                   "选择 ipa")
        self.lbl_info = tk.Label(form, text="", bg=SURFACE, fg=ACCENT_DARK,
                                 font=FONT_SM, anchor="w")
        self.lbl_info.grid(row=1, column=1, sticky="w")

        row_label(form, "认证方式", 2)
        auth_box = ttk.Combobox(form, textvariable=self.var_auth, state="readonly",
                                values=[self.AUTH_API_LABEL, self.AUTH_ACCOUNT_LABEL])
        auth_box.grid(row=2, column=1, sticky="w", pady=5)
        auth_box.bind("<<ComboboxSelected>>", self._on_auth_change)

        # ---- API Key 面板 ----
        self.frm_api = tk.Frame(form, bg=SURFACE)
        self.frm_api.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.frm_api.columnconfigure(1, weight=1)
        row_label(self.frm_api, "密钥类型", 0)
        ttk.Combobox(self.frm_api, textvariable=self.var_kind, state="readonly",
                     values=[self.KIND_TEAM, self.KIND_SELF]).grid(
                         row=0, column=1, sticky="w", pady=5)
        row_label(self.frm_api, "Key ID", 1)
        ttk.Entry(self.frm_api, textvariable=self.var_key_id).grid(
            row=1, column=1, sticky="ew", pady=5)
        row_label(self.frm_api, "Issuer ID", 2)
        ttk.Entry(self.frm_api, textvariable=self.var_issuer).grid(
            row=2, column=1, sticky="ew", pady=5)
        row_label(self.frm_api, "私钥 .p8", 3)
        browse_box(self.frm_api, 3, self.var_key, [("API 私钥", "*.p8"), ("所有文件", "*.*")],
                   "选择 AuthKey_*.p8")

        # ---- Apple ID 面板 ----
        self.frm_account = tk.Frame(form, bg=SURFACE)
        self.frm_account.grid(row=4, column=0, columnspan=2, sticky="ew")
        self.frm_account.columnconfigure(1, weight=1)
        row_label(self.frm_account, "Apple ID", 0)
        ttk.Entry(self.frm_account, textvariable=self.var_apple_id).grid(
            row=0, column=1, sticky="ew", pady=5)
        row_label(self.frm_account, "应用专用密码", 1)
        pwd_box = tk.Frame(self.frm_account, bg=SURFACE)
        pwd_box.grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Entry(pwd_box, textvariable=self.var_password, show="*").pack(
            side="left", fill="x", expand=True)
        pwd_link = tk.Label(pwd_box, text="如何获取？", bg=SURFACE, fg=ACCENT,
                            font=FONT_SM, cursor="hand2")
        pwd_link.pack(side="left", padx=(8, 0))
        pwd_link.bind("<Button-1>", lambda e: AppPasswordDialog(self))
        row_label(self.frm_account, "团队（可选）", 2)
        team_box = tk.Frame(self.frm_account, bg=SURFACE)
        team_box.grid(row=2, column=1, sticky="ew", pady=5)
        self.cmb_team = ttk.Combobox(team_box, textvariable=self.var_team, values=[])
        self.cmb_team.pack(side="left", fill="x", expand=True)
        self.cmb_team.bind("<<ComboboxSelected>>", self._on_team_select)
        self.btn_teams = ttk.Button(team_box, text="获取团队", width=8,
                                    style="Ghost.TButton", command=self._load_teams)
        self.btn_teams.pack(side="left", padx=(6, 0))

        row_label(form, "iTMSTransporter", 5)
        exe_box = tk.Frame(form, bg=SURFACE)
        exe_box.grid(row=5, column=1, sticky="ew", pady=5)
        ttk.Entry(exe_box, textvariable=self.var_exe).pack(
            side="left", fill="x", expand=True)
        ttk.Button(exe_box, text="自动检测", width=8, style="Ghost.TButton",
                   command=self._auto_detect).pack(side="left", padx=(6, 0))
        ttk.Button(exe_box, text="选择目录…", width=9, style="Ghost.TButton",
                   command=self._pick_itms).pack(side="left", padx=(6, 0))
        ttk.Button(exe_box, text="下载", width=5, style="Ghost.TButton",
                   command=lambda: webbrowser.open(U.DOWNLOAD_URL)).pack(side="left", padx=(6, 0))

        tk.Label(self, text="API Key 模式：私钥按 AuthKey_<KeyID>.p8 复制到临时目录，上传结束自动删除。"
                            "Apple ID 模式：需使用应用专用密码（不是登录密码），密码仅在本次上传时传给"
                            " iTMSTransporter，不会被保存。",
                 bg=SURFACE, fg=INK_MUTED, font=FONT_SM, justify="left",
                 wraplength=760).pack(anchor="w", padx=18, pady=(4, 8))

        log_head = tk.Frame(self, bg=SURFACE)
        log_head.pack(fill="x", padx=18)
        tk.Label(log_head, text="上传日志", bg=SURFACE, fg=INK_SOFT,
                 font=FONT_SM).pack(side="left")
        self.lbl_status = tk.Label(log_head, text="", bg=SURFACE, fg=INK_MUTED, font=FONT_SM)
        self.lbl_status.pack(side="right")

        log_wrap = tk.Frame(self, bg=CODE_BG)
        log_wrap.pack(fill="both", expand=True, padx=18, pady=(4, 0))
        self.log_text = tk.Text(log_wrap, height=10, font=FONT_MONO, bg=CODE_BG,
                                fg=CODE_FG, relief="flat", bd=0, padx=10, pady=8,
                                insertbackground=CODE_FG, wrap="none")
        lsb = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log_text.yview,
                            style="Dark.TScrollbar")
        self.log_text.configure(yscrollcommand=lsb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        lsb.pack(side="right", fill="y")
        self.log_text.configure(state="disabled")

        btns = tk.Frame(self, bg=SURFACE)
        btns.pack(fill="x", padx=18, pady=12)
        self.btn_start = ttk.Button(btns, text="开始上传", style="Primary.TButton",
                                    command=self._on_start)
        self.btn_start.pack(side="right")
        ttk.Button(btns, text="关闭", style="Ghost.TButton",
                   command=self._on_close).pack(side="right", padx=8)
        ttk.Button(btns, text="打开 App Store Connect", style="Ghost.TButton",
                   command=lambda: webbrowser.open(
                       "https://appstoreconnect.apple.com/apps")).pack(side="left")
        ttk.Button(btns, text="复制命令", style="Ghost.TButton",
                   command=self._copy_command).pack(side="left", padx=(8, 0))

        self._on_auth_change()
        self.after(120, self._auto_detect)

    # ------------------------------------------------------------------ 组件
    def _link(self, parent, text, url):
        lbl = tk.Label(parent, text=text, bg=SURFACE, fg=ACCENT, font=FONT_SM,
                       cursor="hand2")
        lbl.bind("<Button-1>", lambda e: webbrowser.open(url))
        return lbl

    def _set_status(self, text, color=INK_MUTED):
        self.lbl_status.configure(text=text, fg=color)

    def _log(self, line):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self._log_tail.append(line)
        if len(self._log_tail) > 80:
            del self._log_tail[:-80]

    # ------------------------------------------------------------------ 检测
    def _auto_detect(self):
        try:
            exe = U.find_itms(self.var_exe.get())
        except U.UploadError:
            self._set_status("未检测到，点“选择目录…”或“下载”", WARNING)
            return
        self.var_exe.set(exe)
        self._set_status("已找到 iTMSTransporter", SUCCESS)

    def _pick_itms(self):
        folder = filedialog.askdirectory(title="选择 iTMSTransporter 安装目录")
        if not folder:
            return
        try:
            self.var_exe.set(U.find_itms(folder))
            self._set_status("已找到 iTMSTransporter", SUCCESS)
        except U.UploadError:
            messagebox.showwarning(
                "未找到工具",
                "该目录下没有 iTMSTransporter.cmd，请选择 Apple Transporter 的安装目录。",
                parent=self)

    def _on_auth_change(self, *_):
        if self.var_auth.get() == self.AUTH_ACCOUNT_LABEL:
            self.frm_api.grid_remove()
            self.frm_account.grid()
        else:
            self.frm_account.grid_remove()
            self.frm_api.grid()

    def _persist_form(self, exe: str, password: str = ""):
        kind = "individual" if self.var_kind.get() == self.KIND_SELF else "team"
        account = self.var_auth.get() == self.AUTH_ACCOUNT_LABEL
        data = load_settings()
        data.update({
            "itms_path": exe,
            "asc_key_id": self.var_key_id.get().strip(),
            "asc_issuer": self.var_issuer.get().strip(),
            "asc_key_path": self.var_key.get().strip(),
            "asc_key_type": kind,
            "asc_auth": U.AUTH_ACCOUNT if account else U.AUTH_API,
            "asc_apple_id": self.var_apple_id.get().strip(),
            "asc_team_id": self.var_team.get().strip(),
        })
        if account:
            data["asc_password"] = (base64.b64encode(password.encode("utf-8")).decode("ascii")
                                    if password else "")
        save_settings(data)

    def _on_team_select(self, *_):
        match = re.search(r"(\d{6,12})", self.var_team.get())
        if match:
            self.var_team.set(match.group(1))

    def _load_teams(self):
        if self._teams_loading:
            return
        apple_id = self.var_apple_id.get().strip()
        password = self.var_password.get()
        if not apple_id or not password:
            messagebox.showwarning("获取团队", "请先填写 Apple ID 和应用专用密码", parent=self)
            return
        try:
            exe = U.find_itms(self.var_exe.get().strip())
        except U.UploadError as exc:
            messagebox.showwarning("获取团队", str(exc), parent=self)
            return
        self.var_exe.set(exe)
        self._persist_form(exe, password)
        self._teams_loading = True
        self.btn_teams.configure(text="查询中…", state="disabled")
        self._set_status("正在登录 Apple 并获取团队列表…", ACCENT_DARK)

        def work():
            try:
                teams, lines, code = U.list_providers(
                    exe, apple_id, password,
                    lambda s: self._team_queue.put(("log", s)))
                self._team_queue.put(("teams", teams))
            except Exception as exc:  # noqa: BLE001
                self._team_queue.put(("err", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=work, daemon=True).start()
        self.after(150, self._poll_teams)

    def _poll_teams(self):
        while True:
            try:
                kind, payload = self._team_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._log(payload)
            elif kind == "err":
                self._finish_teams(None, str(payload))
            else:
                self._finish_teams(payload, "")
        if self._teams_loading:
            self.after(150, self._poll_teams)

    def _finish_teams(self, teams, error):
        self._teams_loading = False
        self.btn_teams.configure(text="获取团队", state="normal")
        if error:
            self._set_status("获取团队失败", DANGER)
            hint = U.friendly_error(error) or error
            messagebox.showerror("获取团队", hint, parent=self)
            return
        if not teams:
            self._set_status("未解析到团队，请手动填写团队 ID", WARNING)
            messagebox.showinfo(
                "获取团队",
                "登录成功，但没能从输出中解析出团队。\n"
                "请直接在“团队”里填写团队 ID（App Store Connect 网页地址中那串 10 位字符）。",
                parent=self)
            return
        values = [f"{name} ({tid})" if name else tid for tid, name in teams]
        self.cmb_team.configure(values=values)
        self.cmb_team.set(values[0])
        self.var_team.set(teams[0][0])
        self._persist_form(self.var_exe.get().strip(), self.var_password.get())
        self._set_status(f"已获取 {len(teams)} 个团队", SUCCESS)

    def _load_ipa(self, path):
        try:
            info = U.ipa_info(path)
        except U.UploadError as exc:
            self.lbl_info.configure(text=str(exc), fg=DANGER)
            return
        text = (f"{info['name']}  ·  {info['bundle_id']}  ·  "
                f"版本 {info['version']} ({info['build']})  ·  iOS {info['min_os']}+  ·  "
                f"{human_size(info['size'])}")
        self.lbl_info.configure(text=text, fg=ACCENT_DARK)

    # ------------------------------------------------------------------ 上传
    def _copy_command(self):
        ipa = self.var_ipa.get().strip()
        exe = self.var_exe.get().strip()
        if not ipa or not exe:
            messagebox.showinfo("复制命令", "请先选择 ipa 和 iTMSTransporter", parent=self)
            return
        kind = "individual" if self.var_kind.get() == self.KIND_SELF else "team"
        prefix = ""
        if self.var_auth.get() == self.AUTH_ACCOUNT_LABEL:
            apple_id = self.var_apple_id.get().strip()
            if not apple_id:
                messagebox.showinfo("复制命令", "请先填写 Apple ID", parent=self)
                return
            cmd = U.manual_command(exe, ipa, auth=U.AUTH_ACCOUNT, apple_id=apple_id,
                                   team_id=self.var_team.get().strip())
        else:
            key = self.var_key.get().strip()
            key_id = self.var_key_id.get().strip()
            if not (key and key_id):
                messagebox.showinfo("复制命令", "请先选择私钥并填写 Key ID", parent=self)
                return
            try:
                workspace = U.persist_key_dir(key, key_id, kind)
            except (U.UploadError, OSError) as exc:
                messagebox.showerror("复制命令", str(exc), parent=self)
                return
            prefix = f'cd /d "{workspace}"\r\n'
            cmd = U.manual_command(exe, ipa, key_id, self.var_issuer.get().strip(), kind)
        self.clipboard_clear()
        self.clipboard_append(f"{prefix}{cmd}\r\n")
        self._set_status("命令已复制，粘贴到命令提示符即可手动上传", SUCCESS)

    def _on_start(self):
        if self._worker is not None:
            self._cancel.set()
            self.btn_start.configure(text="正在停止…")
            return

        ipa = self.var_ipa.get().strip()
        if not ipa or not os.path.isfile(ipa):
            messagebox.showwarning("缺少 ipa", "请选择要上传的 .ipa 文件", parent=self)
            return
        try:
            exe = U.find_itms(self.var_exe.get().strip())
        except U.UploadError:
            messagebox.showwarning(
                "缺少工具",
                "未找到 iTMSTransporter。\n可点“选择目录…”指定安装位置"
                "（例如 D:\\softwave\\itms），或点“下载”安装官方 Windows 版。",
                parent=self)
            return
        self.var_exe.set(exe)

        account = self.var_auth.get() == self.AUTH_ACCOUNT_LABEL
        key = key_id = issuer = apple_id = password = team = ""
        kind = "individual" if self.var_kind.get() == self.KIND_SELF else "team"
        if account:
            apple_id = self.var_apple_id.get().strip()
            password = self.var_password.get()
            team = self.var_team.get().strip()
            if not apple_id or not password:
                messagebox.showwarning(
                    "缺少账号信息",
                    "请填写 Apple ID 和应用专用密码（在 appleid.apple.com 生成，不是登录密码）。",
                    parent=self)
                return
        else:
            key = self.var_key.get().strip()
            key_id = self.var_key_id.get().strip()
            issuer = self.var_issuer.get().strip()
            if not key or not os.path.isfile(key):
                messagebox.showwarning("缺少私钥",
                                       "请选择 App Store Connect API 的 .p8 私钥文件",
                                       parent=self)
                return
            if not key_id or not issuer:
                messagebox.showwarning("缺少参数", "请填写 Key ID 和 Issuer ID", parent=self)
                return

        self._persist_form(exe, password)

        self._cancel.clear()
        self._log_tail = []
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._set_status("正在启动 iTMSTransporter…", ACCENT_DARK)
        self.btn_start.configure(text="取消上传")

        def work():
            try:
                code = U.run_upload(
                    exe, ipa, key_id, issuer, key, kind,
                    lambda s: self._queue.put(("log", s)),
                    self._cancel.is_set,
                    auth=U.AUTH_ACCOUNT if account else U.AUTH_API,
                    apple_id=apple_id, password=password, team_id=team)
                self._queue.put(("done", code))
            except U.UploadError as exc:
                self._queue.put(("error", str(exc)))
            except Exception as exc:  # noqa: BLE001
                self._queue.put(("error", f"{type(exc).__name__}: {exc}"))

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()
        self.after(100, self._poll)

    def _poll(self):
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._log(payload)
            elif kind == "error":
                self._finish(False, payload)
            else:
                self._finish(payload == 0, "")
        if self._worker is not None and self._worker.is_alive():
            self.after(120, self._poll)

    def _finish(self, ok, error):
        self._worker = None
        self.btn_start.configure(text="开始上传")
        if self._cancel.is_set():
            self._set_status("已取消上传", WARNING)
            return
        if ok:
            self._set_status("上传成功", SUCCESS)
            messagebox.showinfo(
                "上传成功",
                "ipa 已提交给 App Store Connect。\n"
                "处理通常需要几分钟，处理完成后可在 TestFlight 或版本页看到构建。",
                parent=self)
            return
        tail = "\n".join(self._log_tail[-8:])
        hint = U.friendly_error(tail + "\n" + error) or error or "请查看日志中的错误信息"
        self._set_status("上传失败", DANGER)
        detail = "\n".join(self._log_tail[-6:]).strip()
        message = hint
        if detail and detail not in message:
            message += "\n\n日志末尾：\n" + detail
        messagebox.showerror("上传失败", message, parent=self)

    def _on_close(self):
        if self._worker is not None:
            if not messagebox.askyesno("正在上传", "确定要停止并关闭吗？", parent=self):
                return
            self._cancel.set()
        self.destroy()


class AppPasswordDialog(tk.Toplevel):
    """说明如何生成 App 专用密码。"""

    STEPS = (
        "1. 浏览器打开 appleid.apple.com，登录你 Mac 上登录 Transporter 的开发者账号。\n\n"
        "2. 左侧点「登录与安全」（旧版叫「安全」）。\n\n"
        "3. 找到「应用专用密码」，点「生成应用专用密码」或「+」。\n\n"
        "4. 输入一个名称，例如 Transporter Windows，点「创建」。\n\n"
        "5. 复制生成的密码（形如 abcd-efgh-ijkl-mnop），粘贴到本窗口。\n\n"
        "说明：\n"
        "· 该密码只显示一次，关闭页面后无法再查看，只能重新生成。\n"
        "· 它不是 Apple ID 登录密码；泄漏时可在同一页面撤销，不影响账号本身。\n"
        "· Mac 版 Transporter 收到的短信验证码只对那次登录有效，不能填在这里。\n"
        "· 若页面上找不到「应用专用密码」入口，说明账号未开启双重认证，直接填登录密码即可。"
    )

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.title("如何获取应用专用密码")
        self.geometry("600x480")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.configure(bg=SURFACE)
        self.protocol("WM_DELETE_WINDOW", self._close)

        tk.Frame(self, bg=ACCENT, height=2).pack(fill="x")
        ttk.Label(self, text="如何获取应用专用密码",
                  font=("Microsoft YaHei UI", 13, "bold")).pack(
                      anchor="w", padx=18, pady=(14, 2))
        ttk.Label(self, text="Windows 命令行工具不支持短信验证码，需要用专用密码代替。",
                  style="Sub.TLabel", wraplength=560).pack(anchor="w", padx=18)
        tk.Frame(self, bg=HAIRLINE, height=1).pack(fill="x", padx=18, pady=8)

        tk.Label(self, text=self.STEPS, bg=SURFACE, fg=INK, font=FONT_SM,
                 justify="left", anchor="nw", wraplength=560).pack(
                     fill="both", expand=True, padx=18, pady=(0, 8))

        btns = tk.Frame(self, bg=SURFACE)
        btns.pack(fill="x", padx=18, pady=12)
        ttk.Button(btns, text="打开 appleid.apple.com", style="Primary.TButton",
                   command=lambda: webbrowser.open(
                       "https://appleid.apple.com/account/manage")).pack(side="right")
        ttk.Button(btns, text="知道了", style="Ghost.TButton",
                   command=self._close).pack(side="right", padx=8)

    def _close(self):
        self.destroy()
        try:
            self.parent.grab_set()
        except tk.TclError:
            pass


class UpdateDialog(tk.Toplevel):
    """发现新版本：查看更新说明、下载并替换当前程序。"""

    def __init__(self, app, info: dict):
        super().__init__(app)
        self.app = app
        self.info = info
        self._q = queue.Queue()
        self.title("发现新版本")
        self.geometry("660x660")
        self.minsize(600, 620)
        self.transient(app)
        self.grab_set()
        self.configure(bg=SURFACE)
        self.protocol("WM_DELETE_WINDOW", self._close)

        tk.Frame(self, bg=ACCENT, height=2).pack(fill="x")
        ttk.Label(self, text=f"发现新版本 v{info['version']}",
                  font=("Microsoft YaHei UI", 14, "bold")).pack(
                      anchor="w", padx=18, pady=(16, 2))
        ttk.Label(self, text=f"当前版本 v{__version__}，更新来自 GitHub Releases。",
                  style="Sub.TLabel").pack(anchor="w", padx=18)
        tk.Frame(self, bg=HAIRLINE, height=1).pack(fill="x", padx=18, pady=10)

        tk.Label(self, text="更新说明", bg=SURFACE, fg=INK_SOFT,
                 font=FONT_SM).pack(anchor="w", padx=18)
        notes_wrap = tk.Frame(self, bg=SURFACE_ALT)
        notes_wrap.pack(fill="both", expand=True, padx=18, pady=(4, 10))
        notes = tk.Text(notes_wrap, font=FONT_SM, bg=SURFACE_ALT, fg=INK, relief="flat",
                        bd=0, padx=10, pady=8, wrap="word")
        nbar = ttk.Scrollbar(notes_wrap, orient="vertical", command=notes.yview)
        notes.configure(yscrollcommand=nbar.set)
        notes.pack(side="left", fill="both", expand=True)
        nbar.pack(side="right", fill="y")
        notes.insert("1.0", info.get("notes") or "（该版本没有填写更新说明）")
        notes.configure(state="disabled")

        self.pb = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.lbl_status = tk.Label(self, text="", bg=SURFACE, fg=INK_MUTED, font=FONT_SM)
        self.lbl_status.pack(anchor="w", padx=18)
        tk.Label(self, text="若一直停在“下载中”，多为网络无法访问 GitHub 或速度极慢，"
                           "可关闭本窗口后用「打开发布页」手动下载。",
                 bg=SURFACE, fg=INK_MUTED, font=FONT_SM,
                 wraplength=600, justify="left").pack(anchor="w", padx=18, pady=(2, 0))

        btns = tk.Frame(self, bg=SURFACE)
        btns.pack(fill="x", padx=18, pady=12)
        self.btn_install = ttk.Button(btns, text="立即更新", style="Primary.TButton",
                                      command=self._install)
        self.btn_install.pack(side="right")
        ttk.Button(btns, text="稍后", style="Ghost.TButton",
                   command=self._close).pack(side="right", padx=8)
        ttk.Button(btns, text="打开发布页", style="Ghost.TButton",
                   command=lambda: webbrowser.open(info["page"])).pack(side="left")

        self._cancelled = False
        self._dest = ""
        self._t0 = 0.0

    def _close(self):
        self._cancelled = True
        self.destroy()
        try:
            self.app.grab_set()
        except tk.TclError:
            pass

    def _set_status(self, text, color=INK_MUTED):
        self.lbl_status.configure(text=text, fg=color)

    def _install(self):
        info = self.info
        if not info.get("asset_url"):
            webbrowser.open(info["page"])
            return
        if not UP.can_self_update():
            messagebox.showinfo(
                "无法自动更新",
                "当前以源码方式运行，不能自动替换程序。\n请到发布页下载新版 exe。",
                parent=self)
            webbrowser.open(info["page"])
            return

        self.btn_install.configure(state="disabled", text="下载中…")
        self.pb.pack(fill="x", padx=18, pady=(2, 6))
        self._set_status("正在连接 GitHub…（可关闭窗口取消）", ACCENT_DARK)
        dest = os.path.join(tempfile.gettempdir(),
                            info.get("asset_name") or "AppleProfileTool_update.exe")
        self._dest = dest
        self._t0 = time.time()

        def work():
            try:
                UP.download(info["asset_url"], dest,
                            lambda done, total: self._q.put(("pct", (done, total))),
                            should_stop=lambda: self._cancelled)
            except UP.UpdateError as exc:
                self._q.put(("err", str(exc)))
            else:
                self._q.put(("done", dest))

        threading.Thread(target=work, daemon=True).start()
        self.after(150, self._poll_download)

    def _poll_download(self):
        if not self.winfo_exists():
            self._cancelled = True      # 窗口已销毁：让后台线程尽快停止写入
            return
        finished = False
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "pct":
                    self._on_progress(*payload)
                elif kind == "err":
                    self._download_failed(payload)
                    finished = True
                else:
                    self._download_done(payload)
                    finished = True
        except queue.Empty:
            pass
        if not finished and self.btn_install.cget("state") == "disabled":
            self.after(150, self._poll_download)

    def _on_progress(self, done, total):
        if self._cancelled:
            return
        # GitHub 在国内常见只有几十 KB/s，显示速度才能区分“在下载”和“真卡住”
        elapsed = max(time.time() - self._t0, 0.001)
        kb = done / elapsed / 1024
        speed = f"{kb / 1024:.1f} MB/s" if kb >= 1024 else f"{kb:.0f} KB/s"
        if total:
            percent = int(done * 100 / total)
            self.pb["value"] = percent
            self._set_status(
                f"正在下载… {done / 1048576:.1f} MB / {total / 1048576:.1f} MB"
                f"（{percent}%）· {speed}", ACCENT_DARK)
        else:
            self._set_status(f"正在下载… {done / 1048576:.1f} MB · {speed}", ACCENT_DARK)

    def _download_failed(self, message):
        if self._cancelled or not self.winfo_exists():
            try:
                if self._dest and os.path.exists(self._dest):
                    os.remove(self._dest)
            except OSError:
                pass
            return
        self.pb.pack_forget()
        self.btn_install.configure(state="normal", text="立即更新")
        self._set_status("下载失败", DANGER)
        messagebox.showerror("更新失败", message, parent=self)

    def _download_done(self, path):
        if self._cancelled or not self.winfo_exists():
            try:
                os.remove(path)
            except OSError:
                pass
            return
        self.pb["value"] = 100
        self._set_status("下载完成，正在替换并重启…", SUCCESS)
        try:
            UP.apply_update(path)
        except UP.UpdateError as exc:
            self.btn_install.configure(state="normal", text="立即更新")
            self._set_status("无法自动替换", DANGER)
            messagebox.showerror("更新失败", str(exc), parent=self)
            return
        self.app.after(600, self.app.destroy)


def run():
    app = MainWindow()
    app.mainloop()
