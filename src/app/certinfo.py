# -*- coding: utf-8 -*-
"""读取证书文件并提取备案常用信息。

支持 Apple 的 .p12/.pfx 与 Android 的 .keystore/.jks，以及单独的 .cer/.crt/.der/.pem。
输出 MD5 / SHA-1 / SHA-256 指纹、公钥（Base64）、主题、颁发者、有效期等，
即 APP 备案（工信部）通常需要填写的字段。
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

from . import signing

CERT_RE = re.compile(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.S)
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class CertInfoError(RuntimeError):
    pass


def _run(args, timeout: int = 120):
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout,
                              creationflags=CREATE_NO_WINDOW)
    except FileNotFoundError as exc:
        raise CertInfoError(f"无法启动 {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CertInfoError("命令执行超时") from exc


def _text(raw: bytes) -> str:
    return (raw or b"").decode("utf-8", "replace").strip()


def find_keytool() -> str:
    """查找 keytool（用于 .jks 或 OpenSSL 打不开的 keystore）。"""
    found = shutil.which("keytool")
    if found:
        return found
    candidates = []
    java_home = os.environ.get("JAVA_HOME", "")
    if java_home:
        candidates.append(os.path.join(java_home, "bin", "keytool.exe"))
    for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if not base:
            continue
        java_dir = os.path.join(base, "Java")
        if os.path.isdir(java_dir):
            for name in sorted(os.listdir(java_dir), reverse=True):
                candidates.append(os.path.join(java_dir, name, "bin", "keytool.exe"))
        candidates.append(os.path.join(base, "Common Files", "Oracle", "Java",
                                       "javapath", "keytool.exe"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return ""


def _fingerprint(digest) -> str:
    return ":".join(f"{b:02X}" for b in digest)


def analyze_cert(pem: str, openssl: str, workdir: str) -> dict:
    """解析单张 PEM 证书，返回备案需要的字段。"""
    pem_path = os.path.join(workdir, "one.pem")
    with open(pem_path, "w", encoding="ascii") as fh:
        fh.write(pem.strip() + "\n")

    info: dict = {"pem": pem}

    res = _run([openssl, "x509", "-in", pem_path, "-noout", "-subject", "-issuer",
                "-serial", "-startdate", "-enddate", "-nameopt", "utf8"])
    for line in _text(res.stdout).splitlines():
        if line.startswith("subject="):
            info["subject"] = line[len("subject="):].strip()
        elif line.startswith("issuer="):
            info["issuer"] = line[len("issuer="):].strip()
        elif line.startswith("serial="):
            info["serial"] = line[len("serial="):].strip()
        elif line.startswith("notBefore="):
            info["not_before"] = line[len("notBefore="):].strip()
        elif line.startswith("notAfter="):
            info["not_after"] = line[len("notAfter="):].strip()

    der = _run([openssl, "x509", "-in", pem_path, "-outform", "DER"]).stdout or b""
    if not der:
        raise CertInfoError("无法把证书转换为 DER")
    info["der"] = der
    info["md5"] = _fingerprint(hashlib.md5(der).digest())
    info["sha1"] = _fingerprint(hashlib.sha1(der).digest())
    info["sha256"] = _fingerprint(hashlib.sha256(der).digest())
    info["cert_b64"] = base64.b64encode(der).decode("ascii")

    res = _run([openssl, "x509", "-in", pem_path, "-noout", "-pubkey"])
    match = re.search(r"-----BEGIN PUBLIC KEY-----(.*?)-----END PUBLIC KEY-----",
                      _text(res.stdout), re.S)
    info["public_key_b64"] = re.sub(r"\s+", "", match.group(1)) if match else ""

    body = _text(_run([openssl, "x509", "-in", pem_path, "-noout", "-text"]).stdout)
    match = re.search(r"Signature Algorithm:\s*(\S+)", body)
    info["sig_alg"] = match.group(1) if match else ""
    match = re.search(r"Public Key Algorithm:\s*(\S+)", body)
    info["key_alg"] = match.group(1) if match else ""
    match = re.search(r"Public-Key:\s*\((\d+) bit\)", body)
    info["key_bits"] = match.group(1) if match else ""
    return info


def _pems_from_pkcs12(path: str, password: str, openssl: str, workdir: str) -> list:
    out = os.path.join(workdir, "bag.pem")
    pwd = password or ""
    args = [openssl, "pkcs12", "-in", path, "-nokeys", "-passin", "pass:" + pwd,
            "-out", out]
    res = _run(args)
    if res.returncode != 0 or not os.path.isfile(out):
        # 旧式 RC2-40：借 .NET 重新导出后再读
        converted = os.path.join(workdir, "converted.p12")
        new_pwd = "certinfo-temp"
        try:
            signing.convert_pkcs12_legacy(path, pwd, converted, new_pwd, workdir)
        except signing.SignError as exc:
            raise CertInfoError(str(exc)) from exc
        res = _run([openssl, "pkcs12", "-in", converted, "-nokeys",
                    "-passin", "pass:" + new_pwd, "-out", out])
    if res.returncode != 0 or not os.path.isfile(out):
        detail = _text(res.stderr).splitlines()
        raise CertInfoError(detail[0] if detail else "OpenSSL 无法读取该文件")
    with open(out, "r", encoding="utf-8", errors="ignore") as fh:
        pems = CERT_RE.findall(fh.read())
    if not pems:
        raise CertInfoError("文件中没有可识别的证书")
    return pems


def _pem_from_der(path: str, openssl: str) -> str:
    res = _run([openssl, "x509", "-inform", "DER", "-in", path, "-outform", "PEM"])
    pems = CERT_RE.findall(_text(res.stdout))
    if res.returncode != 0 or not pems:
        raise CertInfoError("不是有效的 DER 证书")
    return pems[0]


def _pems_from_keytool(path: str, password: str) -> list:
    keytool = find_keytool()
    if not keytool:
        raise CertInfoError("未找到 keytool（需要安装 Java 才能读取 JKS）")
    res = _run([keytool, "-list", "-rfc", "-keystore", path,
                "-storepass", password or ""])
    pems = CERT_RE.findall(_text(res.stdout))
    if not pems:
        detail = _text(res.stderr).splitlines()
        raise CertInfoError(detail[0] if detail else "keytool 未能读取证书")
    return pems


def read_certificates(path: str, password: str = "", openssl: str = "") -> tuple:
    """返回 (证书信息列表, 提示文本)。第一张为签名证书，其余为证书链。"""
    if not os.path.isfile(path):
        raise CertInfoError(f"文件不存在：{path}")
    try:
        exe = openssl or signing.find_openssl()
    except signing.OpenSSLNotFound as exc:
        raise CertInfoError(str(exc)) from exc

    with open(path, "rb") as fh:
        raw = fh.read()

    workdir = tempfile.mkdtemp(prefix="certinfo_")
    try:
        pems, note = _collect_pems(path, raw, password, exe, workdir)
        return [analyze_cert(pem, exe, workdir) for pem in pems], note
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _collect_pems(path: str, raw: bytes, password: str, openssl: str,
                  workdir: str) -> tuple:
    if b"-----BEGIN CERTIFICATE-----" in raw:
        pems = CERT_RE.findall(raw.decode("utf-8", "ignore"))
        if pems:
            return pems, ""

    if raw[:4] == b"\xfe\xed\xfe\xed" or path.lower().endswith(".jks"):
        return _pems_from_keytool(path, password), "已通过 keytool 读取"

    errors = []
    try:
        return _pems_from_pkcs12(path, password, openssl, workdir), ""
    except CertInfoError as exc:
        errors.append(f"PKCS#12：{exc}")

    try:
        return [_pem_from_der(path, openssl)], ""
    except CertInfoError as exc:
        errors.append(f"DER 证书：{exc}")

    try:
        return _pems_from_keytool(path, password), "已通过 keytool 读取"
    except CertInfoError as exc:
        errors.append(f"keytool：{exc}")

    raise CertInfoError("无法读取证书，请确认文件类型与密码是否正确。\n" + "\n".join(errors))
