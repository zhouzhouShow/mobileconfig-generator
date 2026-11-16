# -*- coding: utf-8 -*-
"""Apple 配置描述文件（.mobileconfig）数据模型。

包含：payload 类型与字段定义（驱动界面渲染）、构建 plist、解析已有描述文件、基本校验。
"""
from __future__ import annotations

import base64
import datetime
import plistlib
import re
import uuid
from dataclasses import dataclass, field as dc_field
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

DATE_FMT = "%Y-%m-%dT%H:%M:%SZ"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
IDENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def new_uuid() -> str:
    return str(uuid.uuid4()).upper()


def uuid_short(value: str) -> str:
    return (value or "").replace("-", "")[:8]


def iso_now_plus(days: int) -> str:
    dt = datetime.datetime.utcnow() + datetime.timedelta(days=days)
    return dt.strftime(DATE_FMT)


def parse_date(text: str) -> Optional[datetime.datetime]:
    text = (text or "").strip()
    if not text:
        return None
    if DATE_RE.match(text):
        return datetime.datetime.strptime(text, DATE_FMT)
    return None


def _blob_bytes(value: Any) -> Optional[bytes]:
    """把界面里保存的 {'filename','b64'} 结构还原成二进制。"""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, dict) and value.get("b64"):
        try:
            return base64.b64decode(value["b64"])
        except Exception:
            return None
    return None


def _blob_name(value: Any, fallback: str = "") -> str:
    if isinstance(value, dict):
        return value.get("filename") or fallback
    return fallback


def make_blob(path: str, **extra: Any) -> dict:
    with open(path, "rb") as fh:
        raw = fh.read()
    blob = {
        "filename": path.replace("\\", "/").rsplit("/", 1)[-1],
        "b64": base64.b64encode(raw).decode("ascii"),
        "size": len(raw),
    }
    blob.update(extra)
    return blob


def load_certificate_der(path: str) -> bytes:
    """读取 .cer/.crt/.der/.pem，统一返回 DER 字节（PayloadContent 需要原始 DER）。"""
    with open(path, "rb") as fh:
        raw = fh.read()
    if b"-----BEGIN" not in raw:
        return raw
    text = raw.decode("ascii", "ignore")
    blocks = re.findall(r"-----BEGIN [^-]+-----(.*?)-----END [^-]+-----", text, re.S)
    if not blocks:
        raise ValueError("无法在 PEM 文件中找到证书内容")
    body = re.sub(r"\s+", "", blocks[0])
    return base64.b64decode(body)


# ---------------------------------------------------------------------------
# 字段定义
# ---------------------------------------------------------------------------

@dataclass
class Field:
    """界面字段。key 为 plist 键；key 为 None 表示只在界面中使用。"""
    key: Optional[str]
    label: str
    kind: str = "text"          # text/password/multiline/bool/int/choice/date/file/ref
    default: Any = ""
    help: str = ""
    required: bool = False
    options: list = dc_field(default_factory=list)   # [(value, label)]
    show_if: Optional[Callable[[dict], bool]] = None
    group: str = "基本"
    accept: str = ""            # file 类型的后缀过滤


@dataclass
class PayloadSpec:
    key: str
    label: str
    payload_type: str
    identifier_suffix: str
    summary: str
    fields: list
    builder: Callable[["Payload", "Profile"], list]
    parser: Callable[[dict, "Payload"], None]


def _f(*args, **kwargs) -> Field:
    return Field(*args, **kwargs)


# ---------------------------------------------------------------------------
# Wi-Fi
# ---------------------------------------------------------------------------

WIFI_AUTH = [
    ("wpa2", "WPA2 个人版（预共享密钥）"),
    ("wpa3", "WPA3 个人版（预共享密钥）"),
    ("wpa", "WPA 个人版（旧设备）"),
    ("wep", "WEP（旧设备）"),
    ("open", "开放网络（无密码）"),
    ("eap", "企业级 802.1X（需要证书或账号）"),
]


def _wifi_is_personal(d):
    return d.get("auth_mode") in ("wpa2", "wpa3", "wpa", "wep")


def _wifi_is_eap(d):
    return d.get("auth_mode") == "eap"


WIFI_FIELDS = [
    _f("ssid", "网络名称 SSID", "text", required=True, group="网络",
       help="Wi-Fi 的 SSID，区分大小写"),
    _f("auth_mode", "安全类型", "choice", "wpa2", options=WIFI_AUTH, group="网络"),
    _f("password", "密码", "password", group="网络", show_if=_wifi_is_personal,
       help="WPA2/WPA3 密码长度 8–63 位"),
    _f("hidden", "隐藏网络", "bool", False, group="网络",
       help="接入点不广播 SSID 时勾选"),

    _f("proxy_type", "代理类型", "choice", "None",
       options=[("None", "不使用代理"), ("Manual", "手动"), ("Auto", "自动（PAC）")],
       group="代理设置"),
    _f("proxy_server", "代理服务器", "text", group="代理设置",
       show_if=lambda d: d.get("proxy_type") == "Manual"),
    _f("proxy_port", "代理端口", "int", 8080, group="代理设置",
       show_if=lambda d: d.get("proxy_type") == "Manual"),
    _f("proxy_auth", "需要代理认证", "bool", False, group="代理设置",
       show_if=lambda d: d.get("proxy_type") == "Manual"),
    _f("proxy_username", "代理用户名", "text", group="代理设置",
       show_if=lambda d: d.get("proxy_type") == "Manual" and d.get("proxy_auth")),
    _f("proxy_password", "代理密码", "password", group="代理设置",
       show_if=lambda d: d.get("proxy_type") == "Manual" and d.get("proxy_auth")),
    _f("pac_url", "PAC 文件地址", "text", group="代理设置",
       show_if=lambda d: d.get("proxy_type") == "Auto"),
    _f("pac_fallback", "PAC 失败时直连", "bool", True, group="代理设置",
       show_if=lambda d: d.get("proxy_type") == "Auto"),

    _f("eap_type", "EAP 类型", "choice", "peap",
       options=[("peap", "PEAP（用户名 + 密码）"), ("tls", "EAP-TLS（客户端证书）"),
                ("ttls", "EAP-TTLS（用户名 + 密码）")],
       group="企业级 802.1X", show_if=_wifi_is_eap),
    _f("eap_encryption", "无线加密方式", "choice", "WPA2",
       options=[("WPA2", "WPA2"), ("WPA3", "WPA3")],
       group="企业级 802.1X", show_if=_wifi_is_eap),
    _f("eap_username", "用户名", "text", group="企业级 802.1X",
       show_if=lambda d: _wifi_is_eap(d) and d.get("eap_type") in ("peap", "ttls"),
       help="可以写成 域\\用户名 或 用户名@域"),
    _f("eap_password", "密码", "password", group="企业级 802.1X",
       show_if=lambda d: _wifi_is_eap(d) and d.get("eap_type") in ("peap", "ttls")),
    _f("eap_outer", "外部身份", "text", group="企业级 802.1X",
       show_if=lambda d: _wifi_is_eap(d) and d.get("eap_type") in ("peap", "ttls"),
       help="OuterIdentity，留空则使用 anonymous，可避免泄露真实用户名"),
    _f("eap_cert_ref", "客户端证书", "ref", group="企业级 802.1X",
       show_if=lambda d: _wifi_is_eap(d) and d.get("eap_type") == "tls",
       help="需先在左侧添加“PKCS#12 证书”并选择它"),
    _f("tls_trust", "服务器证书校验", "choice", "",
       options=[("", "默认（不写入该项）"),
                ("true", "TLSAllowTrustedServers = true"),
                ("false", "TLSAllowTrustedServers = false")],
       group="企业级 802.1X", show_if=_wifi_is_eap,
       help="不确定时保持“默认”；写入后对应 EAPClientConfiguration 中的布尔值"),
    _f("tls_names", "受信任服务器名称", "text", group="企业级 802.1X",
       show_if=_wifi_is_eap, help="可选，多个用英文逗号分隔，写入 TLSTrustedServerNames"),
]


def build_wifi(p: "Payload", prof: "Profile") -> list:
    d = p.data
    out = _base(p, prof)
    out["SSID_STR"] = d.get("ssid", "")
    mode = d.get("auth_mode") or "wpa2"

    if mode == "wpa2":
        out["EncryptionType"] = "WPA2"
        out["Password"] = d.get("password", "")
    elif mode == "wpa3":
        out["EncryptionType"] = "WPA3"
        out["Password"] = d.get("password", "")
    elif mode == "wpa":
        out["EncryptionType"] = "WPA"
        out["Password"] = d.get("password", "")
    elif mode == "wep":
        out["EncryptionType"] = "WEP"
        out["Password"] = d.get("password", "")
    elif mode == "open":
        out["EncryptionType"] = "None"

    if d.get("hidden"):
        out["HIDDEN_NETWORK"] = True

    proxy = d.get("proxy_type") or "None"
    if proxy == "Manual":
        out["ProxyType"] = "Manual"
        out["ProxyServer"] = d.get("proxy_server", "")
        try:
            out["ProxyServerPort"] = int(d.get("proxy_port") or 0)
        except (TypeError, ValueError):
            out["ProxyServerPort"] = 0
        if d.get("proxy_auth"):
            out["ProxyUsername"] = d.get("proxy_username", "")
            out["ProxyPassword"] = d.get("proxy_password", "")
    elif proxy == "Auto":
        out["ProxyType"] = "Auto"
        out["ProxyPACURL"] = d.get("pac_url", "")
        out["ProxyPACFallbackAllowed"] = bool(d.get("pac_fallback", True))
    else:
        out["ProxyType"] = "None"

    if mode == "eap":
        eap = {"AcceptEAPTypes": []}
        etype = d.get("eap_type") or "peap"
        if etype == "tls":
            eap["AcceptEAPTypes"] = [13]
            cert_uuid = d.get("eap_cert_ref") or ""
            if cert_uuid:
                eap["PayloadCertificateUUID"] = cert_uuid
        elif etype == "ttls":
            eap["AcceptEAPTypes"] = [21]
            eap["TTLSInnerAuthentication"] = "MSCHAPv2"
            eap["UserName"] = d.get("eap_username", "")
            eap["UserPassword"] = d.get("eap_password", "")
        else:
            eap["AcceptEAPTypes"] = [25]
            eap["UserName"] = d.get("eap_username", "")
            eap["UserPassword"] = d.get("eap_password", "")
        if etype in ("peap", "ttls") and d.get("eap_outer"):
            eap["OuterIdentity"] = d.get("eap_outer")
        if d.get("tls_trust") in ("true", "false"):
            eap["TLSAllowTrustedServers"] = d.get("tls_trust") == "true"
        names = [x.strip() for x in (d.get("tls_names") or "").split(",") if x.strip()]
        if names:
            eap["TLSTrustedServerNames"] = names
        out["EAPClientConfiguration"] = eap
        out["EncryptionType"] = d.get("eap_encryption") or "WPA2"

    return [out]


def parse_wifi(src: dict, p: "Payload") -> None:
    d = p.data
    d["ssid"] = src.get("SSID_STR", "")
    enc = src.get("EncryptionType", "WPA2")
    eap = src.get("EAPClientConfiguration")
    if eap:
        d["auth_mode"] = "eap"
        types = eap.get("AcceptEAPTypes") or []
        if 13 in types:
            d["eap_type"] = "tls"
        elif 21 in types:
            d["eap_type"] = "ttls"
        else:
            d["eap_type"] = "peap"
        d["eap_encryption"] = enc if enc in ("WPA2", "WPA3") else "WPA2"
        d["eap_username"] = eap.get("UserName", "") or ""
        d["eap_password"] = eap.get("UserPassword", "") or ""
        d["eap_outer"] = eap.get("OuterIdentity", "") or ""
        d["eap_cert_ref"] = eap.get("PayloadCertificateUUID", "") or ""
        if "TLSAllowTrustedServers" in eap:
            d["tls_trust"] = "true" if eap["TLSAllowTrustedServers"] else "false"
        else:
            d["tls_trust"] = ""
        d["tls_names"] = ", ".join(eap.get("TLSTrustedServerNames") or [])
    else:
        d["auth_mode"] = {"WPA2": "wpa2", "WPA3": "wpa3", "WPA": "wpa",
                          "WEP": "wep", "None": "open"}.get(enc, "wpa2")
    d["password"] = src.get("Password", "") or ""
    d["hidden"] = bool(src.get("HIDDEN_NETWORK"))

    proxy = src.get("ProxyType") or "None"
    if proxy not in ("Manual", "Auto"):
        proxy = "None"
    d["proxy_type"] = proxy
    d["proxy_server"] = src.get("ProxyServer", "") or ""
    d["proxy_port"] = src.get("ProxyServerPort", 8080) or 8080
    d["proxy_auth"] = bool(src.get("ProxyUsername"))
    d["proxy_username"] = src.get("ProxyUsername", "") or ""
    d["proxy_password"] = src.get("ProxyPassword", "") or ""
    d["pac_url"] = src.get("ProxyPACURL", "") or ""
    d["pac_fallback"] = bool(src.get("ProxyPACFallbackAllowed", True))


WIFI_SPEC = PayloadSpec(
    key="wifi", label="Wi-Fi 网络", payload_type="com.apple.wifi.managed",
    identifier_suffix="wifi", summary="Wi-Fi", fields=WIFI_FIELDS,
    builder=build_wifi, parser=parse_wifi,
)


# ---------------------------------------------------------------------------
# 根证书
# ---------------------------------------------------------------------------

def build_rootcert(p: "Payload", prof: "Profile") -> list:
    out = _base(p, prof)
    blob = p.data.get("cert")
    data = _blob_bytes(blob)
    out["PayloadCertificateFileName"] = _blob_name(blob, "root.cer")
    out["PayloadContent"] = data if data is not None else b""
    return [out]


def parse_rootcert(src: dict, p: "Payload") -> None:
    content = src.get("PayloadContent") or b""
    p.data["cert"] = {
        "filename": src.get("PayloadCertificateFileName") or "root.cer",
        "b64": base64.b64encode(content).decode("ascii") if content else "",
        "size": len(content) if content else 0,
        "kind": "cert",
    }


ROOTCERT_FIELDS = [
    _f("cert", "证书文件", "file", required=True, group="证书",
       accept="证书文件|*.cer;*.crt;*.der;*.pem|所有文件|*.*",
       help="支持 DER(.cer/.crt/.der) 与 PEM(.pem)，会自动转换为 DER 存进 PayloadContent"),
]

ROOTCERT_SPEC = PayloadSpec(
    key="rootcert", label="根证书", payload_type="com.apple.security.root",
    identifier_suffix="rootcert", summary="根证书", fields=ROOTCERT_FIELDS,
    builder=build_rootcert, parser=parse_rootcert,
)


# ---------------------------------------------------------------------------
# PKCS#12
# ---------------------------------------------------------------------------

def build_pkcs12(p: "Payload", prof: "Profile") -> list:
    out = _base(p, prof)
    blob = p.data.get("p12")
    out["PayloadCertificateFileName"] = _blob_name(blob, "cert.p12")
    out["PayloadPassword"] = p.data.get("password", "") or ""
    data = _blob_bytes(blob)
    out["PayloadContent"] = data if data is not None else b""
    return [out]


def parse_pkcs12(src: dict, p: "Payload") -> None:
    content = src.get("PayloadContent") or b""
    p.data["p12"] = {
        "filename": src.get("PayloadCertificateFileName") or "cert.p12",
        "b64": base64.b64encode(content).decode("ascii") if content else "",
        "size": len(content) if content else 0,
        "kind": "p12",
    }
    p.data["password"] = src.get("PayloadPassword", "") or ""


PKCS12_FIELDS = [
    _f("p12", "证书文件", "file", required=True, group="证书",
       accept="PKCS#12|*.p12;*.pfx|所有文件|*.*",
       help="包含客户端证书与私钥的 .p12/.pfx 文件"),
    _f("password", "P12 密码", "password", group="证书",
       help="导出时写入 PayloadPassword，iOS 用它解开 P12"),
]

PKCS12_SPEC = PayloadSpec(
    key="pkcs12", label="PKCS#12 证书", payload_type="com.apple.security.pkcs12",
    identifier_suffix="pkcs12", summary="PKCS#12", fields=PKCS12_FIELDS,
    builder=build_pkcs12, parser=parse_pkcs12,
)


# ---------------------------------------------------------------------------
# Web Clip
# ---------------------------------------------------------------------------

def build_webclip(p: "Payload", prof: "Profile") -> list:
    d = p.data
    out = _base(p, prof)
    out["Label"] = d.get("label", "")
    out["URL"] = d.get("url", "")
    icon = _blob_bytes(d.get("icon"))
    if icon:
        out["Icon"] = icon
    out["IsRemovable"] = bool(d.get("is_removable", True))
    if d.get("full_screen"):
        out["FullScreen"] = True
    return [out]


def parse_webclip(src: dict, p: "Payload") -> None:
    p.data["label"] = src.get("Label", "") or ""
    p.data["url"] = src.get("URL", "") or ""
    icon = src.get("Icon") or b""
    p.data["icon"] = {
        "filename": "icon.png",
        "b64": base64.b64encode(icon).decode("ascii") if icon else "",
        "size": len(icon) if icon else 0,
        "kind": "image",
    }
    p.data["is_removable"] = bool(src.get("IsRemovable", True))
    p.data["full_screen"] = bool(src.get("FullScreen", False))


WEBCLIP_FIELDS = [
    _f("label", "图标名称", "text", required=True, group="基本",
       help="显示在 iPhone 主屏上的名称"),
    _f("url", "网址", "text", required=True, group="基本",
       help="必须是 http:// 或 https:// 开头的完整地址"),
    _f("icon", "图标图片", "file", group="外观",
       accept="PNG 图片|*.png|所有文件|*.*",
       help="建议 180×180 或 1024×1024 的 PNG；不填则使用系统默认图标"),
    _f("full_screen", "以全屏方式打开（Web App）", "bool", False, group="外观",
       help="勾选后从主屏打开时不显示 Safari 地址栏（iOS/iPadOS）"),
    _f("is_removable", "允许用户移除", "bool", True, group="外观"),
]

WEBCLIP_SPEC = PayloadSpec(
    key="webclip", label="Web Clip（桌面图标）", payload_type="com.apple.webClip.managed",
    identifier_suffix="webclip", summary="Web Clip", fields=WEBCLIP_FIELDS,
    builder=build_webclip, parser=parse_webclip,
)


# ---------------------------------------------------------------------------
# 访问限制（com.apple.applicationaccess [+ com.apple.mobiledevice.passwordpolicy]）
# ---------------------------------------------------------------------------

RESTRICTION_BOOLS = [
    ("allowCamera", "允许使用相机", True),
    ("allowFaceTime", "允许使用 FaceTime", True),
    ("allowSafari", "允许使用 Safari 浏览器", True),
    ("allowAppInstallation", "允许安装 App", True),
    ("allowAppRemoval", "允许删除 App", True),
    ("allowInAppPurchases", "允许 App 内购买", True),
    ("allowVoiceDialing", "允许语音拨号", True),
    ("allowiTunes", "允许使用 iTunes Store", True),
    ("allowMusicService", "允许使用 Apple Music", True),
    ("allowCloudDocumentSync", "允许 iCloud 文档同步", True),
    ("allowAirPrint", "允许 AirPrint 打印", True),
    ("allowAssistant", "允许使用 Siri", True),
    ("allowScreenShot", "允许截屏", True),
    ("allowAccountModification", "允许修改账户设置", True),
    ("allowOTAPKIUpdates", "允许在线更新证书", True),
    ("allowEnterpriseAppTrust", "允许信任企业级 App", True),
]

RESTRICTIONS_FIELDS = [
    _f(key, label, "bool", default, group="功能限制")
    for key, label, default in RESTRICTION_BOOLS
] + [
    _f("enable_pw_policy", "启用密码策略", "bool", False, group="密码策略",
       help="勾选后额外生成一个 com.apple.mobiledevice.passwordpolicy 子配置项"),
    _f("forcePIN", "强制设置密码", "bool", True, group="密码策略",
       show_if=lambda d: d.get("enable_pw_policy")),
    _f("minLength", "密码最小长度", "int", 6, group="密码策略",
       show_if=lambda d: d.get("enable_pw_policy")),
    _f("minComplexChars", "最少特殊字符数", "int", 1, group="密码策略",
       show_if=lambda d: d.get("enable_pw_policy")),
    _f("requireAlphanumeric", "要求同时包含字母和数字", "bool", False, group="密码策略",
       show_if=lambda d: d.get("enable_pw_policy")),
    _f("allowSimple", "允许简单密码（如 1234）", "bool", False, group="密码策略",
       show_if=lambda d: d.get("enable_pw_policy")),
    _f("maxFailedAttempts", "抹掉设备前错误次数", "int", 0, group="密码策略",
       show_if=lambda d: d.get("enable_pw_policy"), help="0 表示不限制"),
    _f("maxInactivity", "空闲锁屏分钟数", "int", 0, group="密码策略",
       show_if=lambda d: d.get("enable_pw_policy"), help="0 表示不限制"),
    _f("maxPINAgeInDays", "密码有效天数", "int", 0, group="密码策略",
       show_if=lambda d: d.get("enable_pw_policy"), help="0 表示不过期"),
    _f("pinHistory", "历史密码数量", "int", 0, group="密码策略",
       show_if=lambda d: d.get("enable_pw_policy")),
]

PW_POLICY_INT_KEYS = ["minLength", "minComplexChars", "maxFailedAttempts",
                      "maxInactivity", "maxPINAgeInDays", "pinHistory"]


def build_restrictions(p: "Payload", prof: "Profile") -> list:
    d = p.data
    acc = _base(p, prof)
    for key, _label, default in RESTRICTION_BOOLS:
        acc[key] = bool(d.get(key, default))

    result = [acc]
    if d.get("enable_pw_policy"):
        pw = _base(p, prof)
        pw["PayloadType"] = "com.apple.mobiledevice.passwordpolicy"
        pw["PayloadUUID"] = d.get("_pw_uuid") or new_uuid()
        pw["PayloadIdentifier"] = (p.identifier or "profile") + ".passwordpolicy"
        pw["PayloadDisplayName"] = (p.display_name or "访问限制") + " - 密码策略"
        pw["allowSimple"] = bool(d.get("allowSimple", False))
        pw["forcePIN"] = bool(d.get("forcePIN", True))
        pw["requireAlphanumeric"] = bool(d.get("requireAlphanumeric", False))
        for key in PW_POLICY_INT_KEYS:
            try:
                val = int(d.get(key) or 0)
            except (TypeError, ValueError):
                val = 0
            if val > 0:
                pw[key] = val
        result.append(pw)
    return result


def parse_restrictions(src: dict, p: "Payload") -> None:
    d = p.data
    for key, _label, default in RESTRICTION_BOOLS:
        d[key] = bool(src.get(key, default))


def parse_password_policy(src: dict, p: "Payload") -> None:
    d = p.data
    d["enable_pw_policy"] = True
    d["_pw_uuid"] = src.get("PayloadUUID") or new_uuid()
    d["forcePIN"] = bool(src.get("forcePIN", True))
    d["allowSimple"] = bool(src.get("allowSimple", False))
    d["requireAlphanumeric"] = bool(src.get("requireAlphanumeric", False))
    if "maxFailedAttempts" in src:
        d["maxFailedAttempts"] = src.get("maxFailedAttempts") or 0
    if "maxPINRetries" in src and "maxFailedAttempts" not in src:
        d["maxFailedAttempts"] = src.get("maxPINRetries") or 0
    for key in PW_POLICY_INT_KEYS:
        if key in src:
            d[key] = src.get(key) or 0


RESTRICTIONS_SPEC = PayloadSpec(
    key="restrictions", label="访问限制", payload_type="com.apple.applicationaccess",
    identifier_suffix="restrictions", summary="访问限制", fields=RESTRICTIONS_FIELDS,
    builder=build_restrictions, parser=parse_restrictions,
)

PW_POLICY_SPEC = PayloadSpec(
    key="passwordpolicy", label="密码策略", payload_type="com.apple.mobiledevice.passwordpolicy",
    identifier_suffix="passwordpolicy", summary="密码策略", fields=RESTRICTIONS_FIELDS,
    builder=build_restrictions, parser=parse_password_policy,
)


SPECS = {s.key: s for s in
         [WIFI_SPEC, ROOTCERT_SPEC, PKCS12_SPEC, WEBCLIP_SPEC, RESTRICTIONS_SPEC]}
ADDABLE = [WIFI_SPEC, ROOTCERT_SPEC, PKCS12_SPEC, WEBCLIP_SPEC, RESTRICTIONS_SPEC]

# PayloadType -> spec（导入时识别；密码策略单独处理）
TYPE_TO_SPEC = {s.payload_type: s for s in SPECS.values()}


# ---------------------------------------------------------------------------
# Payload / Profile
# ---------------------------------------------------------------------------

def defaults_for(spec_key: str) -> dict:
    spec = SPECS[spec_key]
    data = {}
    for fld in spec.fields:
        if fld.key is None:
            continue
        data[fld.key] = fld.default
    if spec_key == "restrictions":
        data["_pw_uuid"] = new_uuid()
    return data


def _jsonable(obj: Any) -> Any:
    """把 plist 里的 bytes/datetime 转成可存进 JSON 的标记结构。"""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        return {"__bytes__": base64.b64encode(bytes(obj)).decode("ascii")}
    if isinstance(obj, datetime.datetime):
        return {"__date__": obj.strftime(DATE_FMT)}
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    return str(obj)


def _from_jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        if set(obj.keys()) == {"__bytes__"}:
            return base64.b64decode(obj["__bytes__"])
        if set(obj.keys()) == {"__date__"}:
            return parse_date(obj["__date__"]) or obj["__date__"]
        return {k: _from_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_jsonable(v) for v in obj]
    return obj


class Payload:
    def __init__(self, type_key: str, display_name: str = "", identifier: str = "",
                 uid: str = "", data: Optional[dict] = None,
                 extras: Optional[dict] = None, raw: Optional[dict] = None):
        self.type_key = type_key
        ident = uid or new_uuid()
        self.uid = ident                      # 界面内部标识
        self.uuid = ident                     # 写入 plist 的 PayloadUUID
        self.display_name = display_name
        self.identifier = identifier
        self.description = ""
        self.organization = ""
        self.data = data if data is not None else (
            defaults_for(type_key) if type_key in SPECS else {})
        self.extras = extras or {}
        self.raw = raw                        # 未知类型原样保留

    @property
    def spec(self) -> Optional[PayloadSpec]:
        return SPECS.get(self.type_key)

    @property
    def is_raw(self) -> bool:
        return self.type_key == "__raw__"

    def type_label(self) -> str:
        if self.is_raw:
            return (self.raw or {}).get("PayloadType", "未知类型")
        return self.spec.label if self.spec else self.type_key

    def summary(self) -> str:
        if self.is_raw:
            return self.type_label()
        d = self.data
        if self.type_key == "wifi":
            return f"Wi-Fi · {d.get('ssid') or '未命名'}"
        if self.type_key == "rootcert":
            return f"根证书 · {_blob_name(d.get('cert'), '未选择文件')}"
        if self.type_key == "pkcs12":
            return f"PKCS#12 · {_blob_name(d.get('p12'), '未选择文件')}"
        if self.type_key == "webclip":
            return f"Web Clip · {d.get('label') or '未命名'}"
        if self.type_key == "restrictions":
            extra = "（含密码策略）" if d.get("enable_pw_policy") else ""
            return f"访问限制{extra}"
        return self.type_label()

    def to_json(self) -> dict:
        return {
            "type": self.type_key, "uid": self.uid, "uuid": self.uuid,
            "display_name": self.display_name, "identifier": self.identifier,
            "description": self.description, "organization": self.organization,
            "data": _jsonable(self.data), "extras": _jsonable(self.extras),
            "raw": _jsonable(self.raw),
        }

    @classmethod
    def from_json(cls, obj: dict) -> "Payload":
        p = cls(obj.get("type", "__raw__"), obj.get("display_name", ""),
                obj.get("identifier", ""), obj.get("uid", ""),
                _from_jsonable(obj.get("data") or {}),
                _from_jsonable(obj.get("extras") or {}),
                _from_jsonable(obj.get("raw")))
        p.uuid = obj.get("uuid") or p.uid
        p.description = obj.get("description", "")
        p.organization = obj.get("organization", "")
        return p


class Profile:
    def __init__(self):
        pid = new_uuid()
        self.meta = {
            "uuid": pid,
            "display_name": "我的配置描述文件",
            "identifier": "com.example.profile." + uuid_short(pid).lower(),
            "organization": "Example Inc.",
            "description": "",
            "removal_disallowed": False,
            "expiration": "",
            "consent": "",
            "extras": {},
        }
        self.payloads: list = []

    # -- 便捷方法 ---------------------------------------------------------
    def org_slug(self) -> str:
        org = (self.meta.get("organization") or "example").strip()
        slug = re.sub(r"[^A-Za-z0-9]+", "", org).lower()
        return slug or "example"

    def make_identifier(self, spec: PayloadSpec, uid: str) -> str:
        return f"com.{self.org_slug()}.{spec.identifier_suffix}.{uuid_short(uid).lower()}"

    def add_payload(self, spec_key: str) -> Payload:
        spec = SPECS[spec_key]
        p = Payload(spec_key)
        p.display_name = spec.label
        p.identifier = self.make_identifier(spec, p.uid)
        self.payloads.append(p)
        return p

    def find_by_uuid(self, value: str) -> Optional[Payload]:
        for p in self.payloads:
            if p.uuid == value:
                return p
        return None

    def all_uuids(self) -> list:
        out = [self.meta.get("uuid", "")]
        for p in self.payloads:
            out.append(p.uuid)
            if p.type_key == "restrictions" and p.data.get("_pw_uuid"):
                out.append(p.data["_pw_uuid"])
        return out

    def replace_uuid_refs(self, old: str, new: str) -> None:
        for p in self.payloads:
            if p.type_key == "wifi" and p.data.get("eap_cert_ref") == old:
                p.data["eap_cert_ref"] = new

    def to_json(self) -> dict:
        return {
            "format": "apple-mobileconfig-generator/project",
            "version": 1,
            "meta": self.meta,
            "payloads": [p.to_json() for p in self.payloads],
        }

    @classmethod
    def from_json(cls, obj: dict) -> "Profile":
        prof = cls()
        meta = dict(prof.meta)
        meta.update(obj.get("meta") or {})
        prof.meta = meta
        prof.payloads = [Payload.from_json(x) for x in (obj.get("payloads") or [])]
        return prof


def _base(p: Payload, prof: Profile) -> dict:
    out = dict(p.extras)
    out["PayloadType"] = p.spec.payload_type if p.spec else "com.apple.unknown"
    out["PayloadVersion"] = 1
    out["PayloadIdentifier"] = p.identifier
    out["PayloadUUID"] = p.uuid
    out["PayloadDisplayName"] = p.display_name or (p.spec.label if p.spec else "配置项")
    if p.description:
        out["PayloadDescription"] = p.description
    org = p.organization or prof.meta.get("organization", "")
    if org:
        out["PayloadOrganization"] = org
    return out


# ---------------------------------------------------------------------------
# 构建 / 解析
# ---------------------------------------------------------------------------

def payload_dicts(p: Payload, prof: Profile) -> list:
    if p.is_raw:
        raw = dict(p.raw or {})
        raw.setdefault("PayloadType", "com.apple.unknown")
        return [raw]
    spec = p.spec
    if not spec:
        return [dict(p.raw or {})]
    return spec.builder(p, prof)


def build_profile_dict(prof: Profile) -> dict:
    meta = prof.meta
    body = {
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadUUID": meta.get("uuid") or new_uuid(),
        "PayloadIdentifier": meta.get("identifier", ""),
        "PayloadDisplayName": meta.get("display_name") or "配置描述文件",
    }
    if meta.get("organization"):
        body["PayloadOrganization"] = meta["organization"]
    if meta.get("description"):
        body["PayloadDescription"] = meta["description"]
    if meta.get("removal_disallowed"):
        body["PayloadRemovalDisallowed"] = True
    exp = parse_date(meta.get("expiration", ""))
    if exp:
        body["PayloadExpirationDate"] = exp
    if meta.get("consent"):
        body["ConsentText"] = {"default": meta["consent"]}

    content = []
    for p in prof.payloads:
        content.extend(payload_dicts(p, prof))
    body["PayloadContent"] = content

    merged = dict(meta.get("extras") or {})
    merged.update(body)
    return merged


def dump_xml(prof: Profile) -> bytes:
    return plistlib.dumps(build_profile_dict(prof), fmt=plistlib.FMT_XML,
                          sort_keys=False)


COMMON_PAYLOAD_KEYS = {
    "PayloadType", "PayloadVersion", "PayloadIdentifier", "PayloadUUID",
    "PayloadDisplayName", "PayloadDescription", "PayloadOrganization",
    "PayloadContent", "PayloadCertificateFileName", "PayloadPassword",
}

# 各类型里由本工具解析/重建的键，导入时不再放进 extras，避免重复或残留
HANDLED_KEYS = {
    "wifi": {"SSID_STR", "EncryptionType", "Password", "HIDDEN_NETWORK", "ProxyType",
             "ProxyServer", "ProxyServerPort", "ProxyUsername", "ProxyPassword",
             "ProxyPACURL", "ProxyPACFallbackAllowed", "EAPClientConfiguration"},
    "rootcert": {"PayloadCertificateFileName"},
    "pkcs12": {"PayloadCertificateFileName", "PayloadPassword"},
    "webclip": {"Label", "URL", "Icon", "IsRemovable", "FullScreen"},
    "restrictions": ({k for k, _l, _d in RESTRICTION_BOOLS}
                     | {"allowSimple", "forcePIN", "requireAlphanumeric", "maxPINRetries"}
                     | set(PW_POLICY_INT_KEYS)),
}


def parse_profile(raw_bytes: bytes) -> tuple:
    """解析 .mobileconfig，返回 (Profile, warnings)。未知 payload 原样保留。"""
    try:
        src = plistlib.loads(raw_bytes)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"无法解析该文件（不是有效的 plist）：{exc}") from exc
    if not isinstance(src, dict):
        raise ValueError("文件内容不是 plist 字典，可能不是配置描述文件")

    prof = Profile()
    warnings = []
    meta = prof.meta
    meta["uuid"] = src.get("PayloadUUID") or new_uuid()
    meta["display_name"] = src.get("PayloadDisplayName") or "导入的配置描述文件"
    meta["identifier"] = src.get("PayloadIdentifier") or (
        "com.example.profile." + uuid_short(meta["uuid"]).lower())
    meta["organization"] = src.get("PayloadOrganization") or ""
    meta["description"] = src.get("PayloadDescription") or ""
    meta["removal_disallowed"] = bool(src.get("PayloadRemovalDisallowed"))
    exp = src.get("PayloadExpirationDate")
    if isinstance(exp, datetime.datetime):
        meta["expiration"] = exp.strftime(DATE_FMT)
    elif isinstance(exp, str):
        meta["expiration"] = exp
    else:
        meta["expiration"] = ""
    consent = src.get("ConsentText")
    if isinstance(consent, dict):
        meta["consent"] = consent.get("default", "")
    elif isinstance(consent, str):
        meta["consent"] = consent
    else:
        meta["consent"] = ""

    known_top = {"PayloadType", "PayloadVersion", "PayloadUUID", "PayloadIdentifier",
                 "PayloadDisplayName", "PayloadOrganization", "PayloadDescription",
                 "PayloadRemovalDisallowed", "PayloadExpirationDate", "ConsentText",
                 "PayloadContent"}
    meta["extras"] = {k: v for k, v in src.items() if k not in known_top}

    restrictions_payload = None
    for item in src.get("PayloadContent") or []:
        if not isinstance(item, dict):
            warnings.append("PayloadContent 中存在非字典条目，已忽略")
            continue
        ptype = item.get("PayloadType") or ""
        if ptype == "com.apple.mobiledevice.passwordpolicy":
            if restrictions_payload is None:
                restrictions_payload = Payload(
                    "restrictions", "访问限制",
                    "com.example.restrictions." + uuid_short(new_uuid()).lower())
                prof.payloads.append(restrictions_payload)
            PW_POLICY_SPEC.parser(item, restrictions_payload)
            continue
        spec = TYPE_TO_SPEC.get(ptype)
        if spec is None:
            p = Payload("__raw__", item.get("PayloadDisplayName") or ptype or "未知配置项",
                        item.get("PayloadIdentifier") or "", item.get("PayloadUUID") or "",
                        raw=item)
            prof.payloads.append(p)
            warnings.append(f"不支持的配置项类型 {ptype or '(空)'}，将原样保留")
        else:
            p = Payload(spec.key, item.get("PayloadDisplayName") or spec.label,
                        item.get("PayloadIdentifier") or "", item.get("PayloadUUID") or "")
            p.description = item.get("PayloadDescription") or ""
            p.organization = item.get("PayloadOrganization") or ""
            if spec.key == "restrictions" and restrictions_payload is not None:
                # 密码策略先出现时，合并到同一个“访问限制”配置项里
                p = restrictions_payload
                p.uuid = item.get("PayloadUUID") or p.uuid
                p.uid = p.uuid
                p.display_name = item.get("PayloadDisplayName") or p.display_name
                p.identifier = item.get("PayloadIdentifier") or p.identifier
                p.description = item.get("PayloadDescription") or ""
                p.organization = item.get("PayloadOrganization") or ""
                spec.parser(item, p)
                p.extras = {k: v for k, v in item.items() if k not in COMMON_PAYLOAD_KEYS}
                continue
            spec.parser(item, p)
            skip = COMMON_PAYLOAD_KEYS | HANDLED_KEYS.get(spec.key, set())
            p.extras = {k: v for k, v in item.items() if k not in skip}
            if spec.key == "restrictions":
                restrictions_payload = p
            prof.payloads.append(p)
    return prof, warnings


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    level: str      # "error" | "warn"
    message: str
    where: str = ""


def validate(prof: Profile) -> list:
    issues: list[Issue] = []
    meta = prof.meta

    if not (meta.get("display_name") or "").strip():
        issues.append(Issue("error", "全局：配置名称不能为空", "全局"))
    ident = (meta.get("identifier") or "").strip()
    if not ident:
        issues.append(Issue("error", "全局：PayloadIdentifier 不能为空", "全局"))
    elif not IDENT_RE.match(ident) or "." not in ident:
        issues.append(Issue("error",
                            "全局：PayloadIdentifier 只能包含字母、数字、点、下划线、连字符，且需含点（如 com.example.profile）",
                            "全局"))
    if not (meta.get("organization") or "").strip():
        issues.append(Issue("warn", "全局：建议填写组织名称（PayloadOrganization）", "全局"))

    exp_text = (meta.get("expiration") or "").strip()
    if exp_text:
        exp = parse_date(exp_text)
        if exp is None:
            issues.append(Issue("error", "全局：到期时间格式应为 2027-12-31T00:00:00Z", "全局"))
        elif exp < datetime.datetime.utcnow():
            issues.append(Issue("warn", "全局：到期时间已过，设备可能拒绝安装", "全局"))

    if not prof.payloads:
        issues.append(Issue("warn", "还没有任何配置项，导出的描述文件没有实际内容", "全局"))

    seen_uuid = {}
    seen_ident = {}
    for p in prof.payloads:
        where = p.display_name or p.type_label()
        if not (p.display_name or "").strip():
            issues.append(Issue("error", f"{where}：显示名称不能为空", where))
        if not (p.identifier or "").strip():
            issues.append(Issue("error", f"{where}：PayloadIdentifier 不能为空", where))
        elif not IDENT_RE.match(p.identifier):
            issues.append(Issue("error", f"{where}：PayloadIdentifier 含非法字符", where))
        if not (p.uuid or "").strip():
            issues.append(Issue("error", f"{where}：PayloadUUID 不能为空", where))
        elif p.uuid in seen_uuid:
            issues.append(Issue("error",
                                f"{where}：PayloadUUID 与「{seen_uuid[p.uuid]}」重复", where))
        else:
            seen_uuid[p.uuid] = where
        if p.identifier in seen_ident:
            issues.append(Issue("error",
                                f"{where}：PayloadIdentifier 与「{seen_ident[p.identifier]}」重复", where))
        else:
            seen_ident[p.identifier] = where

        if p.is_raw:
            if not (p.raw or {}).get("PayloadType"):
                issues.append(Issue("error", f"{where}：缺少 PayloadType", where))
            continue

        d = p.data
        if p.type_key == "wifi":
            if not (d.get("ssid") or "").strip():
                issues.append(Issue("error", "Wi-Fi：SSID 不能为空", where))
            mode = d.get("auth_mode")
            if mode in ("wpa2", "wpa3"):
                pw = d.get("password") or ""
                if not pw:
                    issues.append(Issue("error", "Wi-Fi：WPA2/WPA3 需要填写密码", where))
                elif len(pw) < 8:
                    issues.append(Issue("error", "Wi-Fi：WPA2/WPA3 密码至少 8 位", where))
                elif len(pw) > 63:
                    issues.append(Issue("error", "Wi-Fi：WPA2/WPA3 密码不能超过 63 位", where))
            if mode == "eap":
                etype = d.get("eap_type")
                if etype == "tls":
                    ref = d.get("eap_cert_ref") or ""
                    target = prof.find_by_uuid(ref)
                    if not ref:
                        issues.append(Issue("error",
                                            "Wi-Fi：EAP-TLS 需要选择客户端证书（PKCS#12 配置项）", where))
                    elif target is None:
                        issues.append(Issue("error",
                                            "Wi-Fi：引用的客户端证书已不存在，请重新选择", where))
                    elif target.type_key != "pkcs12":
                        issues.append(Issue("error",
                                            "Wi-Fi：客户端证书必须引用 PKCS#12 配置项", where))
                else:
                    if not (d.get("eap_username") or "").strip():
                        issues.append(Issue("error", "Wi-Fi：企业级认证需要填写用户名", where))
                    if not (d.get("eap_password") or ""):
                        issues.append(Issue("warn", "Wi-Fi：企业级认证未填写密码", where))
            if (d.get("proxy_type") or "None") == "Manual":
                if not (d.get("proxy_server") or "").strip():
                    issues.append(Issue("error", "Wi-Fi：手动代理需要填写服务器地址", where))
            if (d.get("proxy_type") or "None") == "Auto" and not (d.get("pac_url") or "").strip():
                issues.append(Issue("error", "Wi-Fi：自动代理需要填写 PAC 地址", where))
        elif p.type_key == "rootcert":
            if not _blob_bytes(d.get("cert")):
                issues.append(Issue("error", "根证书：请选择证书文件", where))
        elif p.type_key == "pkcs12":
            if not _blob_bytes(d.get("p12")):
                issues.append(Issue("error", "PKCS#12：请选择 .p12/.pfx 文件", where))
            elif not (d.get("password") or ""):
                issues.append(Issue("warn", "PKCS#12：未填写 P12 密码，若文件有密码设备将无法解开", where))
        elif p.type_key == "webclip":
            if not (d.get("label") or "").strip():
                issues.append(Issue("error", "Web Clip：名称不能为空", where))
            url = (d.get("url") or "").strip()
            if not url:
                issues.append(Issue("error", "Web Clip：网址不能为空", where))
            elif not re.match(r"^https?://\S+", url, re.I):
                issues.append(Issue("error", "Web Clip：网址需以 http:// 或 https:// 开头", where))
            icon = _blob_bytes(d.get("icon"))
            if icon and not icon.startswith(b"\x89PNG"):
                issues.append(Issue("warn", "Web Clip：图标建议使用 PNG 格式", where))
        elif p.type_key == "restrictions":
            if not any(bool(d.get(k, v)) != v for k, _l, v in RESTRICTION_BOOLS) and not d.get("enable_pw_policy"):
                issues.append(Issue("warn", "访问限制：所有项目均为“允许”，该配置项没有限制作用", where))
    return issues


def issues_summary(issues: list) -> tuple:
    errors = [i for i in issues if i.level == "error"]
    warns = [i for i in issues if i.level == "warn"]
    return errors, warns
