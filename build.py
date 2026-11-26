# -*- coding: utf-8 -*-
"""把 src/main.py 打包成单文件 exe（PyInstaller）。

用法：python build.py
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = "Apple描述文件生成器"
# 交给 PyInstaller 的名字必须是纯 ASCII：Windows CI 的控制台编码会把中文名截成 "Apple"
BUILD_NAME = "AppleProfileTool"


def main() -> int:
    os.chdir(ROOT)
    for folder in ("build", "dist"):
        if os.path.isdir(folder):
            shutil.rmtree(folder, ignore_errors=True)

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", BUILD_NAME,
        "--paths", "src",
        "--hidden-import", "app.profile",
        "--hidden-import", "app.ui",
        "--hidden-import", "app.signing",
        "--hidden-import", "app.certinfo",
        "--hidden-import", "app.upload",
        "--hidden-import", "app.update",
        "--hidden-import", "tkinterdnd2",
        # tkinterdnd2 会带上 tkdnd 的 DLL/tcl 脚本，必须一并收集才能拖拽
        "--collect-all", "tkinterdnd2",
        os.path.join("src", "main.py"),
    ]
    print(" ".join(args))
    code = subprocess.call(args)
    if code != 0:
        print("打包失败")
        return code
    exe = os.path.join("dist", NAME + ".exe")
    os.replace(os.path.join("dist", BUILD_NAME + ".exe"), exe)
    print(f"\n打包完成：{exe}（{os.path.getsize(exe) / 1024 / 1024:.1f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
