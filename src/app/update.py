# -*- coding: utf-8 -*-
"""检查并安装 GitHub Releases 上的新版本。"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = "zhouzhouShow/mobileconfig-generator"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"

_CREATE_NO_WINDOW = 0x08000000

# 等待旧进程退出 → 覆盖 exe → 重新启动；全部使用 ASCII，避免命令行编码问题
_UPDATE_SCRIPT = r"""@echo off
setlocal
:wait
tasklist /FI "PID eq %1" 2>nul | find "%1" >nul
if not errorlevel 1 (
  ping -n 2 127.0.0.1 >nul
  goto wait
)
copy /y "%2" "%3" >nul
if "%~4"=="1" start "" "%3"
del "%~f0"
"""


class UpdateError(Exception):
    """更新相关错误。"""


def parse_version(text: str) -> tuple:
    numbers = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in numbers[:4]) or (0,)


def is_newer(latest: str, current: str) -> bool:
    def norm(text):
        nums = list(parse_version(text))
        while nums and nums[-1] == 0:
            nums.pop()
        return tuple(nums)

    return norm(latest) > norm(current)


def check(current: str, timeout: int = 12):
    """查询最新 Release。有新版本返回信息字典，没有返回 None。"""
    request = urllib.request.Request(
        API_LATEST,
        headers={
            "User-Agent": "AppleProfileTool/" + current,
            "Accept": "application/vnd.github+json",
        })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("仓库还没有发布任何 Release，暂无更新可检查")
        raise UpdateError(f"GitHub 返回 HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001
        raise UpdateError(str(exc))

    tag = str(data.get("tag_name", "")).lstrip("vV")
    if not tag or not is_newer(tag, current):
        return None

    asset_url = ""
    asset_name = ""
    for asset in data.get("assets") or []:
        name = str(asset.get("name", ""))
        if name.lower().endswith(".exe"):
            asset_url = asset.get("browser_download_url", "")
            asset_name = name
            break
    return {
        "version": tag,
        "notes": str(data.get("body") or ""),
        "page": str(data.get("html_url") or RELEASES_PAGE),
        "asset_url": asset_url,
        "asset_name": asset_name,
    }


def download(url: str, dest: str, progress=None, should_stop=None) -> str:
    """下载更新包。

    progress(done_bytes, total_bytes) 用于界面显示，total 为 0 表示长度未知；
    should_stop() 返回 True 时中止下载。
    """
    request = urllib.request.Request(url, headers={"User-Agent": "AppleProfileTool"})
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=20) as response, \
                open(dest, "wb") as fh:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if should_stop and should_stop():
                    raise UpdateError("已取消下载")
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
                # 极慢网络保护：30 分钟仍未完成就放弃，提示手动下载
                if time.time() - started > 1800:
                    raise UpdateError("下载超过 30 分钟仍未完成。\n"
                                      "请关闭本窗口，点「打开发布页」手动下载 exe 覆盖。")
    except UpdateError:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("更新包不存在（HTTP 404），可能该版本已删除，请到发布页手动下载")
        raise UpdateError(f"下载失败：HTTP {exc.code}")
    except (socket.timeout, TimeoutError):
        raise UpdateError("下载超时：网络无法稳定连接 GitHub 下载服务器。\n"
                          "请关闭本窗口，点「打开发布页」手动下载 exe 覆盖。")
    except Exception as exc:  # noqa: BLE001
        raise UpdateError(f"下载失败：{exc}")
    if os.path.getsize(dest) <= 0:
        raise UpdateError("下载内容为空，请重试或手动下载")
    return dest


def can_self_update() -> bool:
    return bool(getattr(sys, "frozen", False))


def apply_update(new_exe: str, restart: bool = True) -> None:
    """用新的 exe 替换当前程序：启动一个后台脚本，等本进程退出后覆盖并重启。"""
    if not can_self_update():
        raise UpdateError("当前是源码运行模式，请手动替换程序文件")
    target = sys.executable
    folder = os.path.dirname(target) or "."
    try:
        probe = os.path.join(folder, ".update_write_test")
        with open(probe, "w") as fh:
            fh.write("x")
        os.remove(probe)
    except OSError:
        raise UpdateError(f"程序所在目录不可写，无法自动更新：\n{folder}\n"
                          "请把程序移到有权限的目录，或手动下载覆盖。")

    script = os.path.join(tempfile.gettempdir(), "app_update.cmd")
    with open(script, "w", encoding="ascii", errors="ignore") as fh:
        fh.write(_UPDATE_SCRIPT)
    subprocess.Popen(
        ["cmd.exe", "/c", script, str(os.getpid()), new_exe, target,
         "1" if restart else "0"],
        creationflags=_CREATE_NO_WINDOW,
        close_fds=True,
    )
