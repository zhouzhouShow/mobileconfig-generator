# -*- coding: utf-8 -*-
"""调用本机 OpenSSL 对 .mobileconfig 做 S/MIME 签名（SHA-256 / DER）。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class OpenSSLNotFound(RuntimeError):
    pass


class SignError(RuntimeError):
    def __init__(self, message: str, log: str = ""):
        super().__init__(message)
        self.log = log


def _exe_names() -> list:
    if sys.platform == "win32":
        return ["openssl.exe", "openssl"]
    return ["openssl"]


def _app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def candidate_paths() -> list:
    out = []
    app_dir = _app_dir()
    bundle = getattr(sys, "_MEIPASS", "")
    for base in [app_dir, bundle]:
        if not base:
            continue
        for sub in ("tools/openssl", "openssl", "tools"):
            for name in _exe_names():
                out.append(os.path.join(base, sub, name))

    # Git for Windows 自带 openssl
    for exe in ("git.exe", "git"):
        git = shutil.which(exe)
        if git:
            root = os.path.abspath(os.path.join(os.path.dirname(git), ".."))
            out.append(os.path.join(root, "usr", "bin", "openssl.exe"))
            out.append(os.path.join(root, "mingw64", "bin", "openssl.exe"))

    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(env)
        if not base:
            continue
        out.append(os.path.join(base, "OpenSSL-Win64", "bin", "openssl.exe"))
        out.append(os.path.join(base, "OpenSSL-Win32", "bin", "openssl.exe"))
        out.append(os.path.join(base, "Git", "usr", "bin", "openssl.exe"))
        out.append(os.path.join(base, "Git", "mingw64", "bin", "openssl.exe"))
        out.append(os.path.join(base, "OpenSSL", "bin", "openssl.exe"))
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if base:
            out.append(os.path.join(base, "Shining Light Productions", "Win64 OpenSSL", "bin", "openssl.exe"))
    return out


def find_openssl(preferred: str = "") -> str:
    """按顺序查找 openssl 可执行文件，找不到抛 OpenSSLNotFound。"""
    if preferred and os.path.isfile(preferred):
        return preferred
    found = shutil.which("openssl")
    if found:
        return found
    for path in candidate_paths():
        if path and os.path.isfile(path):
            return path
    raise OpenSSLNotFound(
        "没有找到 OpenSSL。\n\n"
        "你可以：\n"
        "1) 安装 OpenSSL（如 Win64 OpenSSL Light Installer），装好后重新打开本程序；\n"
        "2) 或安装 Git for Windows（自带 openssl）；\n"
        "3) 或在签名窗口里手动指定 openssl.exe 的路径。\n\n"
        "找不到 OpenSSL 时仍然可以导出未签名的 .mobileconfig。")


def openssl_version(exe: str) -> str:
    try:
        res = subprocess.run([exe, "version"], capture_output=True, text=True,
                             timeout=20, creationflags=CREATE_NO_WINDOW)
        return (res.stdout or res.stderr).strip()
    except Exception:
        return ""


def _run(args: list, password: str = "") -> str:
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=180,
                             creationflags=CREATE_NO_WINDOW)
    except FileNotFoundError as exc:
        raise OpenSSLNotFound(f"无法启动 OpenSSL：{args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SignError("OpenSSL 执行超时") from exc
    log = ((res.stdout or "") + (res.stderr or "")).strip()
    if res.returncode != 0:
        safe = log.replace(password, "******") if password else log
        raise SignError(f"OpenSSL 返回错误（退出码 {res.returncode}）", safe)
    return log


def _export_from_p12(openssl: str, p12_path: str, password: str, workdir: str):
    cert_pem = os.path.join(workdir, "signer_cert.pem")
    key_pem = os.path.join(workdir, "signer_key.pem")
    passin = "pass:" + password
    _run([openssl, "pkcs12", "-in", p12_path, "-clcerts", "-nokeys",
          "-out", cert_pem, "-passin", passin], password)
    _run([openssl, "pkcs12", "-in", p12_path, "-nocerts", "-nodes",
          "-out", key_pem, "-passin", passin], password)
    return cert_pem, key_pem


def extract_signed_content(raw: bytes, openssl: str = "") -> bytes:
    """从已签名的 .mobileconfig（CMS/DER 或 PEM）里取出原始 plist；失败返回 b''。"""
    try:
        exe = openssl or find_openssl()
    except OpenSSLNotFound:
        return b""
    workdir = tempfile.mkdtemp(prefix="mcextract_")
    try:
        src = os.path.join(workdir, "signed.mobileconfig")
        with open(src, "wb") as fh:
            fh.write(raw)
        for inform in ("DER", "PEM"):
            out = os.path.join(workdir, f"out_{inform}.plist")
            try:
                _run([exe, "smime", "-verify", "-noverify", "-inform", inform,
                      "-in", src, "-out", out])
            except (SignError, OpenSSLNotFound):
                continue
            if os.path.isfile(out) and os.path.getsize(out) > 0:
                with open(out, "rb") as fh:
                    return fh.read()
        return b""
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def sign_file(openssl: str, src: str, out: str, cert_path: str, key_path: str = "",
              chain_path: str = "", cert_password: str = "", key_password: str = "") -> dict:
    """对 src 签名并写入 out（DER）。返回 {'log':..., 'verified': bool}。"""
    workdir = tempfile.mkdtemp(prefix="mcsign_")
    try:
        cert = cert_path
        key = key_path
        if cert_path.lower().endswith((".p12", ".pfx")):
            cert, key = _export_from_p12(openssl, cert_path, cert_password, workdir)
        elif not key:
            raise SignError("请指定签名私钥文件（.key/.pem），或改用 .p12 证书")
        if not os.path.isfile(cert):
            raise SignError(f"找不到证书文件：{cert}")
        if not os.path.isfile(key):
            raise SignError(f"找不到私钥文件：{key}")

        args = [openssl, "smime", "-sign",
                "-in", src,
                "-signer", cert,
                "-inkey", key,
                "-outform", "DER",
                "-nodetach",
                "-md", "sha256",
                "-out", out]
        if chain_path:
            if not os.path.isfile(chain_path):
                raise SignError(f"找不到中间证书文件：{chain_path}")
            args += ["-certfile", chain_path]
        if key_password:
            args += ["-passin", "pass:" + key_password]
        log = _run(args, key_password)

        verified = False
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            verify_out = os.path.join(workdir, "verify.txt")
            try:
                _run([openssl, "smime", "-verify", "-inform", "DER", "-in", out,
                      "-noverify", "-out", verify_out])
                verified = True
            except SignError:
                verified = False
        return {"log": log, "verified": verified,
                "size": os.path.getsize(out) if os.path.isfile(out) else 0}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
