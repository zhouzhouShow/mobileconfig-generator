# -*- coding: utf-8 -*-
"""中文界面：左侧配置项列表、中间表单、右侧 XML 实时预览。"""
from __future__ import annotations

import base64
import json
import os
import plistlib
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from . import profile as P
from . import signing

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

BG = "#f5f6f8"


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
        self.geometry("1440x880")
        self.minsize(1180, 720)
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
        self.status.configure(text=f"已加载 {os.path.basename(path)}", foreground="#137333")
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
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure(".", font=FONT)
        style.configure("TButton", padding=(10, 5))
        style.configure("Tool.TButton", padding=(12, 6))
        style.configure("TLabelframe.Label", font=FONT, foreground="#1f4e79")
        style.configure("Treeview", font=FONT, rowheight=28)
        style.configure("Head.TLabel", font=("Microsoft YaHei UI", 11, "bold"),
                        foreground="#1f4e79")
        style.configure("Help.TLabel", font=FONT_SM, foreground="#6b7076")
        style.configure("Status.TLabel", font=FONT_SM, foreground="#3c4043")

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
        m_tool.add_command(label="检测 OpenSSL 环境", command=self.detect_openssl)
        m_tool.add_command(label="设置 OpenSSL 路径…", command=self.pick_openssl)
        menubar.add_cascade(label="工具", menu=m_tool)

        m_help = tk.Menu(menubar, tearoff=0, font=FONT)
        m_help.add_command(label="使用说明", command=self.show_help)
        m_help.add_command(label="关于", command=self.show_about)
        menubar.add_cascade(label="帮助", menu=m_help)

        self.config(menu=menubar)

    # ------------------------------------------------------------------ 工具栏
    def _build_toolbar(self):
        bar = tk.Frame(self, bg=BG)
        bar.pack(side="top", fill="x", padx=10, pady=(10, 4))

        ttk.Button(bar, text="新建", style="Tool.TButton",
                   command=self.new_profile).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="打开工程", style="Tool.TButton",
                   command=self.open_project).pack(side="left", padx=6)
        ttk.Button(bar, text="保存工程", style="Tool.TButton",
                   command=self.save_project).pack(side="left", padx=6)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(bar, text="导入 .mobileconfig", style="Tool.TButton",
                   command=self.import_mobileconfig).pack(side="left", padx=6)
        ttk.Button(bar, text="导出 .mobileconfig", style="Tool.TButton",
                   command=self.export_unsigned).pack(side="left", padx=6)
        ttk.Button(bar, text="签名并导出", style="Tool.TButton",
                   command=self.open_sign_dialog).pack(side="left", padx=6)

        self.openssl_label = ttk.Label(bar, text="", style="Help.TLabel")
        self.openssl_label.pack(side="right")
        self._update_openssl_label()

    def _update_openssl_label(self):
        path = load_settings().get("openssl_path", "")
        try:
            exe = signing.find_openssl(path)
            ver = signing.openssl_version(exe)
            self.openssl_label.configure(
                text="OpenSSL：已就绪" + (f"（{ver.split(' ')[1]}）" if ver else ""),
                foreground="#137333")
        except signing.OpenSSLNotFound:
            self.openssl_label.configure(text="OpenSSL：未找到（仍可导出未签名版本）",
                                         foreground="#b06000")

    # ------------------------------------------------------------------ 主体
    def _build_body(self):
        body = tk.Frame(self, bg=BG)
        body.pack(side="top", fill="both", expand=True, padx=10, pady=4)

        outer = ttk.PanedWindow(body, orient="horizontal")
        outer.pack(fill="both", expand=True)

        # 左：配置项列表
        left = ttk.Frame(outer, width=250)
        left.pack_propagate(False)
        outer.add(left, weight=0)
        ttk.Label(left, text="配置项", style="Head.TLabel").pack(anchor="w", pady=(4, 6))

        tree_box = ttk.Frame(left)
        tree_box.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_box, show="tree", selectmode="browse")
        tsb = ttk.Scrollbar(tree_box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.tag_configure("global", foreground="#1f4e79")

        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=8)

        self.add_btn = ttk.Menubutton(btns, text="＋ 添加配置项")
        add_menu = tk.Menu(self.add_btn, tearoff=0, font=FONT)
        for spec in P.ADDABLE:
            add_menu.add_command(label=f"{spec.label}", command=lambda k=spec.key: self.add_payload(k))
        self.add_btn["menu"] = add_menu
        self.add_btn.pack(side="left")

        ttk.Button(btns, text="删除", width=6,
                   command=self.delete_payload).pack(side="left", padx=4)
        ttk.Button(btns, text="↑", width=3,
                   command=lambda: self.move_payload(-1)).pack(side="left")
        ttk.Button(btns, text="↓", width=3,
                   command=lambda: self.move_payload(1)).pack(side="left", padx=2)

        # 右：表单 + 预览
        right = ttk.PanedWindow(outer, orient="horizontal")
        outer.add(right, weight=5)

        form_wrap = ttk.Frame(right, width=580)
        right.add(form_wrap, weight=4)
        ttk.Label(form_wrap, text="详细设置", style="Head.TLabel").pack(anchor="w", pady=(4, 4))
        self.form_canvas = tk.Canvas(form_wrap, bg="white", highlightthickness=1,
                                     highlightbackground="#dcdfe3", width=620)
        fsb = ttk.Scrollbar(form_wrap, orient="vertical", command=self.form_canvas.yview)
        self.form_canvas.configure(yscrollcommand=fsb.set)
        self.form_canvas.pack(side="left", fill="both", expand=True)
        fsb.pack(side="right", fill="y")
        self.form_body = tk.Frame(self.form_canvas, bg="white")
        self._form_window = self.form_canvas.create_window((0, 0), window=self.form_body, anchor="nw")
        self.form_body.bind("<Configure>",
                            lambda e: self.form_canvas.configure(scrollregion=self.form_canvas.bbox("all")))
        self.form_canvas.bind("<Configure>",
                             lambda e: self.form_canvas.itemconfigure(self._form_window, width=e.width))
        self.form_canvas.bind("<Enter>", lambda e: self._bind_wheel(True))
        self.form_canvas.bind("<Leave>", lambda e: self._bind_wheel(False))

        preview_wrap = ttk.Frame(right)
        right.add(preview_wrap, weight=5)
        head = ttk.Frame(preview_wrap)
        head.pack(side="top", fill="x", pady=(4, 4))
        ttk.Label(head, text="XML 预览", style="Head.TLabel").pack(side="left")
        self.preview_info = ttk.Label(head, text="", style="Help.TLabel")
        self.preview_info.pack(side="right")

        issues_box = ttk.LabelFrame(preview_wrap, text=" 检查结果 ")
        issues_box.pack(side="bottom", fill="x", pady=(8, 4))
        self.issues_text = tk.Text(issues_box, height=5, font=FONT_SM, relief="flat",
                                   bg="#fbfbfc", padx=6, pady=4, wrap="word")
        self.issues_text.pack(fill="x")
        self.issues_text.tag_configure("error", foreground="#c5221f")
        self.issues_text.tag_configure("warn", foreground="#b06000")
        self.issues_text.tag_configure("ok", foreground="#137333")
        self.issues_text.configure(state="disabled")

        pbody = ttk.Frame(preview_wrap)
        pbody.pack(side="top", fill="both", expand=True)
        self.preview = tk.Text(pbody, wrap="none", font=FONT_MONO, bg="#fbfbfc",
                               foreground="#2b2f33", relief="flat", padx=8, pady=6,
                               undo=False, width=44)
        psb = ttk.Scrollbar(pbody, orient="vertical", command=self.preview.yview)
        hsb = ttk.Scrollbar(pbody, orient="horizontal", command=self.preview.xview)
        self.preview.configure(yscrollcommand=psb.set, xscrollcommand=hsb.set)
        psb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self.preview.pack(side="left", fill="both", expand=True)
        self.preview.tag_configure("key", foreground="#0b5cad")
        self.preview.tag_configure("tag", foreground="#8250df")
        self.preview.tag_configure("err", foreground="#c5221f")
        self.preview.configure(state="disabled")

    def _bind_wheel(self, on: bool):
        if on:
            self.form_canvas.bind_all("<MouseWheel>", self._on_form_wheel)
        else:
            self.form_canvas.unbind_all("<MouseWheel>")

    def _on_form_wheel(self, event):
        self.form_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _build_status(self):
        self.status = ttk.Label(self, text="就绪", style="Status.TLabel", anchor="w")
        self.status.pack(side="bottom", fill="x", padx=12, pady=(0, 6))

    # ------------------------------------------------------------------ 列表
    def refresh_tree(self, select: str = None):
        self.tree.delete(*self.tree.get_children())
        self.tree.insert("", "end", iid="global", text="  ⚙  全局设置", tags=("global",))
        for p in self.profile.payloads:
            self.tree.insert("", "end", iid=p.uid, text=f"  •  {p.summary()}")
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

        header = ttk.Frame(self.form_body)
        header.pack(fill="x", padx=12, pady=(12, 4))
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
        ttk.Label(header, text=title, font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        ttk.Label(header, text=sub, style="Help.TLabel").pack(anchor="w")
        ttk.Separator(self.form_body).pack(fill="x", padx=12, pady=(6, 2))

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
            txt = tk.Text(box, height=18, font=FONT_MONO, wrap="char")
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
        frame = ttk.LabelFrame(self.form_body, text=" " + title + " ")
        frame.pack(fill="x", padx=10, pady=(8, 4))
        holder = ttk.Frame(frame)
        holder.pack(fill="x", pady=4)
        holder.columnconfigure(0, minsize=150)
        holder.columnconfigure(1, weight=1)
        return holder

    def _row(self, parent):
        idx = parent.grid_size()[1]
        outer = ttk.Frame(parent)
        outer.grid(row=idx, column=0, columnspan=2, sticky="ew", padx=6, pady=4)
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
        txt = tk.Text(box, height=4, font=FONT, wrap="word", relief="solid", borderwidth=1)
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
                frame.pack(fill="x", padx=10, pady=(8, 4))
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
                foreground="#c5221f")
        elif warns:
            self.status.configure(
                text=f"共 {n} 个配置项 · 警告 {len(warns)} 个 —— 可以导出",
                foreground="#b06000")
        else:
            self.status.configure(text=f"共 {n} 个配置项 · 检查通过，可以导出",
                                  foreground="#137333")

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
        self.status.configure(text=f"工程已保存到 {path}", foreground="#137333")
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
        self.status.configure(text=f"已导出 {path}", foreground="#137333")

    # ------------------------------------------------------------------ 签名
    def open_sign_dialog(self):
        SignDialog(self)

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
        self.geometry("640x560")
        self.resizable(False, False)
        self.transient(app)
        self.grab_set()
        self.configure(bg=BG)

        cfg = load_settings()
        self.var_cert = tk.StringVar()
        self.var_key = tk.StringVar()
        self.var_chain = tk.StringVar()
        self.var_pass = tk.StringVar()
        self.var_openssl = tk.StringVar(value=cfg.get("openssl_path", ""))
        self.var_out = tk.StringVar(value="")

        pad = {"padx": 14, "pady": 4}
        ttk.Label(self, text="使用 OpenSSL 对描述文件做 S/MIME 签名（SHA-256，DER 输出）",
                  style="Head.TLabel").pack(anchor="w", padx=14, pady=(14, 2))
        ttk.Label(self, text="iOS 只在描述文件带有效签名时才显示“已验证”。",
                  style="Help.TLabel").pack(anchor="w", padx=14, pady=(0, 8))

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
        btns.pack(fill="x", side="bottom", pady=14, padx=14)
        ttk.Button(btns, text="开始签名并导出", command=self.do_sign).pack(side="right")
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right", padx=8)
        ttk.Button(btns, text="仅导出未签名", command=self.export_plain).pack(side="left")

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
            self.openssl_status.configure(text=f"✓ {exe}  {ver}", foreground="#137333")
            return exe
        except signing.OpenSSLNotFound:
            self.openssl_status.configure(
                text="✗ 未找到 OpenSSL：可以点“仅导出未签名”，或先安装 OpenSSL / 手动指定路径",
                foreground="#c5221f")
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
            self.app.status.configure(text="正在签名…", foreground="#1f4e79")
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
        self.app.status.configure(text=f"已签名导出 {out}", foreground="#137333")
        self.destroy()


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
        "下次“打开工程”即可继续编辑。"
    )

    def __init__(self, app):
        super().__init__(app)
        self.title("使用说明")
        self.geometry("760x620")
        self.transient(app)
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=12, pady=12)
        txt = tk.Text(frame, wrap="word", font=FONT, relief="flat", padx=10, pady=8)
        txt.insert("1.0", self.TEXT)
        txt.configure(state="disabled")
        sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")


def run():
    app = MainWindow()
    app.mainloop()
