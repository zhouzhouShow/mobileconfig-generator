# -*- coding: utf-8 -*-
"""界面级检查：文件选择写回、Web Clip 图标导出、拖拽加载与非法类型提示。

用法：python tests/ui_check.py
"""
import os
import plistlib
import sys
import tempfile
from tkinter import filedialog, messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from app import profile as P          # noqa: E402
from app import ui                    # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  [OK]   {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


def fake_event(data: str):
    class Ev:
        pass

    ev = Ev()
    ev.data = data
    return ev


def main() -> int:
    win = ui.MainWindow()
    win.withdraw()
    win.update()

    print("\n== 1. 文件选择按钮 ==")
    tmp = tempfile.mkdtemp(prefix="mcuicheck_")
    png = os.path.join(tmp, "logo.png")
    icon = b"\x89PNG\r\n\x1a\n" + b"hello-icon"
    with open(png, "wb") as fh:
        fh.write(icon)

    p = win.profile.add_payload("webclip")
    p.data.update({"label": "门户", "url": "https://portal.example.com"})
    win.current = p.uid
    win.render_form()
    fld = [f for f in p.spec.fields if f.key == "icon"][0]
    lbl = ttk.Label(win)

    orig = filedialog.askopenfilename
    filedialog.askopenfilename = lambda *a, **k: png
    try:
        win._pick_file(fld, p.data, lbl)
    except Exception as exc:  # noqa: BLE001
        check("点“选择…”不抛异常", False, repr(exc))
    finally:
        filedialog.askopenfilename = orig
    blob = p.data.get("icon") or {}
    check("图标数据写回 payload", blob.get("filename") == "logo.png", blob)
    raw = P.dump_xml(win.profile)
    got = plistlib.loads(raw)["PayloadContent"][0].get("Icon")
    check("导出 plist 的 Icon 为原始 PNG 字节", got == icon, got)

    print("\n== 2. 拖拽 ==")
    check("拖拽已启用（tkinterdnd2 加载成功）", bool(getattr(win, "dnd_ready", False)))
    dnd_map = getattr(win, "_dnd_map", {})
    check("根窗口与主要控件都注册为拖放目标（子控件也覆盖）",
          str(win) in dnd_map and str(win.tree) in dnd_map
          and str(win.preview) in dnd_map and str(win.form_canvas) in dnd_map,
          f"{len(dnd_map)} 个控件")
    check("控件树被完整注册（>40 个）", len(dnd_map) > 40, len(dnd_map))
    sample = os.path.join(ROOT, "examples", "wifi.mobileconfig")
    win.dirty = False
    ret = win._on_drop(fake_event("{" + sample + "}"))
    check("拖入 .mobileconfig 被接受", ret == "copy", ret)
    check("拖入后加载了示例内容",
          win.profile.meta.get("display_name") == "公司 Wi-Fi 配置",
          win.profile.meta.get("display_name"))

    shown = {}
    orig_info = messagebox.showinfo

    def fake_info(title, msg, **kwargs):
        shown["msg"] = msg
        return "ok"

    messagebox.showinfo = fake_info
    try:
        win._on_drop(fake_event("{" + png + "}"))
    finally:
        messagebox.showinfo = orig_info
    check("拖入非 .mobileconfig 给出提示", "不支持的文件类型" in shown.get("msg", ""),
          shown.get("msg"))
    check("非法拖入不改变当前配置",
          win.profile.meta.get("display_name") == "公司 Wi-Fi 配置")

    win.destroy()
    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：" + "、".join(FAILED))
        return 1
    print("界面检查全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
