# -*- coding: utf-8 -*-
"""程序入口。"""
import os
import sys

# 让“源码运行”和“PyInstaller 单文件”两种方式都能找到 app 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:  # Windows 高 DPI 下字体更清晰
    import ctypes

    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

from app.ui import MainWindow  # noqa: E402

def main():
    initial = sys.argv[1] if len(sys.argv) > 1 else ""
    app = MainWindow(initial)
    app.mainloop()


if __name__ == "__main__":
    main()
