# -*- coding: utf-8 -*-
"""把 .ipa 上传到 App Store Connect。

底层调用 Apple 官方免费的 Windows 版 iTMSTransporter（Transporter 4.3），
支持两种认证：App Store Connect API Key（推荐）或 Apple ID + 应用专用密码。
"""
from __future__ import annotations

import locale
import os
import plistlib
import re
import shutil
import subprocess
import tempfile
import zipfile

DOWNLOAD_URL = (
    "https://itunesconnect.apple.com/WebObjects/iTunesConnect.woa/ra/resources/"
    "download/public/Transporter__Windows/bin"
)
GUIDE_URL = "https://help.apple.com/itc/transporteruserguide/"

AUTH_API = "apikey"
AUTH_ACCOUNT = "account"

_CREATE_NO_WINDOW = 0x08000000


class UploadError(Exception):
    """上传相关错误，message 可直接展示给用户。"""


# --------------------------------------------------------------------- 查找工具
def _registry_install_dirs() -> list:
    """从卸载注册表里找 iTMSTransporter 的安装目录。"""
    try:
        import winreg
    except ImportError:
        return []
    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    dirs = []
    for hive, path in roots:
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        with key:
            for index in range(winreg.QueryInfoKey(key)[0]):
                try:
                    sub = winreg.OpenKey(key, winreg.EnumKey(key, index))
                except OSError:
                    continue
                with sub:
                    def read(name):
                        try:
                            return str(winreg.QueryValueEx(sub, name)[0])
                        except OSError:
                            return ""

                    if "transporter" not in read("DisplayName").lower():
                        continue
                    for value in (read("UninstallString"), read("DisplayIcon")):
                        value = value.strip().strip('"')
                        if not value:
                            continue
                        value = value.split(" /")[0]
                        folder = os.path.dirname(value)
                        if folder:
                            dirs.append(folder)
    return dirs


def find_itms(preferred: str = "") -> str:
    """返回 iTMSTransporter 可执行文件路径，找不到时抛 UploadError。"""
    candidates = []
    preferred = (preferred or "").strip().strip('"')
    if preferred:
        candidates.append(preferred)
        candidates.append(os.path.join(preferred, "iTMSTransporter.cmd"))
    for name in ("iTMSTransporter.cmd", "iTMSTransporter.exe", "iTMSTransporter"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    for folder in _registry_install_dirs():
        candidates.append(os.path.join(folder, "iTMSTransporter.cmd"))
        candidates.append(os.path.join(folder, "iTMSTransporter.exe"))
    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    for root in roots:
        if not root:
            continue
        for sub in ("itmstransporter", "iTMSTransporter", "Transporter"):
            candidates.append(os.path.join(root, sub, "iTMSTransporter.cmd"))
            candidates.append(os.path.join(root, "Programs", sub, "iTMSTransporter.cmd"))
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise UploadError(
        "未找到 iTMSTransporter。请点“选择…”指定安装目录（例如 D:\\softwave\\itms），"
        "或安装 Apple 官方的 Windows 版 Transporter 4.3。"
    )


# --------------------------------------------------------------------- 解析 ipa
def ipa_info(path: str) -> dict:
    """读取 ipa 中主 App 的 Info.plist，返回常用字段。"""
    if not os.path.isfile(path):
        raise UploadError("文件不存在：" + path)
    try:
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist()
                     if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", n)]
            if not names:
                raise UploadError("不是有效的 ipa：找不到 Payload/*.app/Info.plist")
            info = plistlib.loads(zf.read(names[0]))
    except zipfile.BadZipFile:
        raise UploadError("不是有效的 zip/ipa 文件")
    except UploadError:
        raise
    except Exception as exc:
        raise UploadError(f"解析 ipa 失败：{exc}")

    return {
        "name": info.get("CFBundleDisplayName") or info.get("CFBundleName") or "",
        "bundle_id": info.get("CFBundleIdentifier", ""),
        "version": info.get("CFBundleShortVersionString", ""),
        "build": str(info.get("CFBundleVersion", "")),
        "min_os": info.get("MinimumOSVersion", ""),
        "size": os.path.getsize(path),
    }


# --------------------------------------------------------------------- 上传
def _upload_args(ipa: str, auth: str, key_id: str, issuer: str, key_type: str,
                 apple_id: str, password: str, team_id: str) -> list:
    args = ["-m", "upload", "-assetFile", ipa]
    if auth == AUTH_ACCOUNT:
        args += ["-u", apple_id]
        if password:
            args += ["-p", password]
        else:
            args.append("-p")      # 不带值：由 iTMSTransporter 交互输入
        if team_id:
            args += ["-asc_provider", team_id]
    else:
        args += ["-apiKey", key_id, "-apiIssuer", issuer, "-apiKeyType", key_type]
    args += ["-v", "informational", "-WONoPause", "true"]
    return args


def build_command(exe: str, ipa: str, key_id: str = "", issuer: str = "",
                  key_type: str = "team", auth: str = AUTH_API,
                  apple_id: str = "", password: str = "",
                  team_id: str = "") -> list:
    args = _upload_args(ipa, auth, key_id, issuer, key_type,
                        apple_id, password, team_id)
    if exe.lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c", exe] + args
    return [exe] + args


def manual_command(exe: str, ipa: str, key_id: str = "", issuer: str = "",
                   key_type: str = "team", auth: str = AUTH_API,
                   apple_id: str = "", team_id: str = "") -> str:
    """给用户复制到命令行的等价命令（排障用，账号模式不写入密码）。"""
    args = _upload_args(ipa, auth, key_id, issuer, key_type, apple_id, "", team_id)
    parts = [f'"{exe}"']
    for item in args:
        parts.append(f'"{item}"' if " " in item else item)
    return " ".join(parts)


def _copy_key(workspace: str, key_path: str, key_id: str, key_type: str) -> str:
    if not os.path.isfile(key_path):
        raise UploadError("API 私钥文件不存在：" + key_path)
    if not key_id:
        raise UploadError("请填写 API Key ID")
    key_dir = os.path.join(workspace, "private_keys")
    os.makedirs(key_dir, exist_ok=True)
    prefix = "ApiKey_" if key_type == "individual" else "AuthKey_"
    shutil.copyfile(key_path, os.path.join(key_dir, prefix + key_id + ".p8"))
    return workspace


def persist_key_dir(key_path: str, key_id: str, key_type: str) -> str:
    """固定目录版：供“复制命令”手动执行时使用。"""
    base = os.path.join(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
                        "AppleProfileTool", "upload_keys")
    try:
        return _copy_key(base, key_path, key_id, key_type)
    except OSError:
        # 目录不可写时退回临时目录，命令里会带上实际路径
        return _copy_key(tempfile.mkdtemp(prefix="asc_keys_"), key_path, key_id, key_type)


def _run_capture(cmd: list, cwd: str, log=None, should_stop=None):
    """执行命令并逐行回调，返回 (退出码, 全部输出行)。"""
    encoding = locale.getpreferredencoding(False) or "utf-8"
    lines = []
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=_CREATE_NO_WINDOW,
    )
    assert proc.stdout is not None
    for raw in iter(proc.stdout.readline, b""):
        if should_stop and should_stop():
            proc.terminate()
            break
        line = raw.decode(encoding, errors="replace").rstrip()
        if line:
            lines.append(line)
            if log:
                log(line)
    return proc.wait(), lines


def run_upload(exe: str, ipa: str, key_id: str = "", issuer: str = "",
               key_path: str = "", key_type: str = "team", log=None,
               should_stop=None, auth: str = AUTH_API, apple_id: str = "",
               password: str = "", team_id: str = "") -> int:
    """执行上传，日志逐行回调。返回退出码，0 表示成功。"""
    if not os.path.isfile(ipa):
        raise UploadError("ipa 文件不存在：" + ipa)
    log = log or (lambda line: None)

    if auth == AUTH_ACCOUNT:
        if not apple_id:
            raise UploadError("请填写 Apple ID")
        if not password:
            raise UploadError("请填写应用专用密码")
        workspace = tempfile.mkdtemp(prefix="asc_upload_")
    else:
        if not issuer:
            raise UploadError("请填写 Issuer ID")
        workspace = _copy_key(tempfile.mkdtemp(prefix="asc_upload_"),
                              key_path, key_id, key_type)

    cmd = build_command(exe, ipa, key_id, issuer, key_type,
                        auth, apple_id, password, team_id)
    try:
        code, _ = _run_capture(cmd, workspace, log, should_stop)
        return code
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


# --------------------------------------------------------------------- 团队列表
_PROVIDER_ROW = re.compile(r"^\s*(?:\d+[.)]?\s+)?(\S.*?)\s{2,}([A-Z0-9]{6,12})\s*$")
_PROVIDER_PARAM = re.compile(r"parameter\s+(\S.*?)\s*=\s*([A-Z0-9]{6,12})\s*$")
_PROVIDER_PREFIX = re.compile(r"^\[[^\]]+\]\s*<[^>]+>\s*\S+:\s*")


def parse_providers(lines) -> list:
    """从 -m provider 的输出里提取 (团队短名, 团队名称)。

    实际输出形如：
        Provider listing:
           - Long Name -                    - Short Name -
        1  Guangzhou Haozhifu ... Co., Ltd  S9MQ53S44J
    """
    table = {}
    params = {}

    def clean_name(name):
        name = name.strip()
        return name if name and not name.startswith("-") else ""

    for raw in lines:
        text = _PROVIDER_PREFIX.sub("", raw.strip())
        if not text:
            continue
        match = _PROVIDER_PARAM.search(text)
        if match:
            name = clean_name(match.group(1))
            if name and "arguments" not in name.lower() and "mode" not in name.lower():
                params.setdefault(match.group(2), name)
            continue
        match = _PROVIDER_ROW.match(text)
        if match:
            name = clean_name(match.group(1))
            if "short name" in name.lower():
                continue
            table.setdefault(match.group(2), name)

    result = table or params
    return [(tid, name) for tid, name in result.items()]


def list_providers(exe: str, apple_id: str, password: str,
                   log=None) -> tuple:
    """用 Apple ID 登录，返回 (团队列表, 原始输出行)。"""
    if not apple_id or not password:
        raise UploadError("请填写 Apple ID 和应用专用密码")
    workspace = tempfile.mkdtemp(prefix="asc_provider_")
    args = ["-m", "provider", "-u", apple_id, "-p", password, "-WONoPause", "true"]
    if exe.lower().endswith((".cmd", ".bat")):
        cmd = ["cmd.exe", "/c", exe] + args
    else:
        cmd = [exe] + args
    try:
        code, lines = _run_capture(cmd, workspace, log)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    return parse_providers(lines), lines, code


# --------------------------------------------------------------------- 错误翻译
_HINTS = (
    ("lock manager", "iTMSTransporter 缓存被占用：请关闭其他 Transporter 窗口或命令行实例后重试；"
                     "仍失败时用“复制命令”在 cmd 中手动执行"),
    ("client configuration failed",
     "认证失败：Key ID、Issuer ID 与 .p8 需来自同一把 App Store Connect API 密钥；"
     "若走代理，请确认代理允许访问 Apple 的上传域名"),
    ("entered incorrectly", "Apple ID 或密码不正确：请先在 appleid.apple.com 生成“应用专用密码”，"
                            "不要填 Apple ID 登录密码"),
    ("app-specific password", "需要使用“应用专用密码”：在 appleid.apple.com → 登录与安全 中生成"),
    ("no providers", "账号下没有可用的团队：请确认该 Apple ID 已加入开发者计划"),
    ("multiple", "账号属于多个团队：请在“团队短名”里填写 App Store Connect 的团队 ID"),
    ("invalid api key", "API Key 无效：Key ID 与 .p8 文件不匹配，或密钥已被撤销"),
    ("unauthorized", "认证失败：请检查 Issuer ID、Key ID 与 .p8 是否属于同一密钥"),
    ("forbidden", "没有权限：请确认该 API Key 具备 App Manager 权限"),
    ("bundle id", "Bundle ID 在 App Store Connect 中不存在，请先创建 App 记录"),
    ("bundle version", "构建号重复：请提高 Info.plist 里的版本号或 Build 号"),
    ("does not contain", "ipa 内容不完整，请确认是用发布证书正常导出的包"),
    ("no suitable application records", "找不到对应 App 记录，请先在 App Store Connect 创建 App"),
    ("invalid signature", "签名无效：ipa 需用 App Store 发布证书与描述文件重签"),
    ("network", "网络连接失败：请检查代理或防火墙是否拦截 Apple 上传域名"),
)


def friendly_error(text: str) -> str:
    low = (text or "").lower()
    for key, hint in _HINTS:
        if key in low:
            return hint
    return ""
