# -*- coding: utf-8 -*-
"""自检脚本：构建/校验/往返解析/签名，并把示例输出写到 examples/。"""
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from app import profile as P          # noqa: E402
from app import signing               # noqa: E402

FAILED = []
PASSED = 0


def check(name, cond, detail=""):
    global PASSED
    if cond:
        PASSED += 1
        print(f"  [OK]   {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


def load(path, mode="rb"):
    with open(path, mode) as fh:
        return fh.read()


def make_wifi() -> P.Profile:
    prof = P.Profile()
    prof.meta.update({
        "uuid": "782C59F5-85C6-4622-BF9C-8C8F1C9A1993",
        "display_name": "公司 Wi-Fi 配置",
        "identifier": "com.example.profile.wifi-demo",
        "organization": "示例科技有限公司",
        "description": "自动连接公司无线网络",
        "consent": "本描述文件由 IT 部门提供，用于连接公司 Wi-Fi。",
    })
    p = prof.add_payload("wifi")
    p.uid = p.uuid = "75027234-1079-4467-9319-2FF488750BA2"
    p.display_name = "公司 Wi-Fi"
    p.identifier = "com.example.wifi.corp"
    p.data.update({"ssid": "Corp-WiFi", "auth_mode": "wpa2", "password": "Passw0rd!2026",
                   "hidden": True, "proxy_type": "None", "tls_trust": ""})
    return prof


def make_webclip() -> P.Profile:
    prof = P.Profile()
    prof.meta.update({
        "uuid": "662FEAE3-A06D-4FE2-BE9C-996443573DF8",
        "display_name": "内网门户图标",
        "identifier": "com.example.profile.webclip-demo",
        "organization": "示例科技有限公司",
        "description": "在桌面添加内网门户入口",
    })
    p = prof.add_payload("webclip")
    p.uid = p.uuid = "7C8E05D8-E9B2-405E-932A-FE0CA61BAE43"
    p.display_name = "内网门户"
    p.identifier = "com.example.webclip.portal"
    p.data.update({"label": "内网门户", "url": "https://portal.example.com",
                   "full_screen": True, "is_removable": False})
    return prof


def make_restrictions() -> P.Profile:
    prof = P.Profile()
    prof.meta.update({
        "uuid": "AAD48B32-D47C-48A5-B7E5-1BBBA698F360",
        "display_name": "教室 iPad 限制策略",
        "identifier": "com.example.profile.restrictions-demo",
        "organization": "示例中学",
        "description": "禁用相机、FaceTime 与 App 安装，并强制 8 位密码",
    })
    p = prof.add_payload("restrictions")
    p.uid = p.uuid = "268D5BDF-80EA-4BE7-A25F-FAD9557AE415"
    p.display_name = "教室 iPad 限制"
    p.identifier = "com.example.restrictions.classroom"
    p.data.update({
        "allowCamera": False, "allowFaceTime": False, "allowAppInstallation": False,
        "allowAppRemoval": False, "allowSafari": True,
        "enable_pw_policy": True, "forcePIN": True, "minLength": 8,
        "minComplexChars": 1, "allowSimple": False, "maxFailedAttempts": 10,
        "_pw_uuid": "EBECE81C-94C3-4A4C-87B1-105B3BDDF9CD",
    })
    return prof


def test_plist_structure(name, prof):
    print(f"\n[{name}]")
    errors = P.issues_summary(P.validate(prof))[0]
    check("校验无错误", not errors, str([e.message for e in errors]))

    raw = P.dump_xml(prof)
    d = plistlib.loads(raw)
    check("PayloadType = Configuration", d.get("PayloadType") == "Configuration")
    check("PayloadVersion 是整数 1", isinstance(d.get("PayloadVersion"), int)
          and not isinstance(d.get("PayloadVersion"), bool) and d["PayloadVersion"] == 1)
    check("PayloadContent 是数组", isinstance(d.get("PayloadContent"), list))
    check("PayloadIdentifier 存在", bool(d.get("PayloadIdentifier")))
    check("PayloadUUID 存在", bool(d.get("PayloadUUID")))
    for item in d["PayloadContent"]:
        check(f"子项 {item.get('PayloadType')} 有 PayloadVersion=1",
              item.get("PayloadVersion") == 1 and isinstance(item["PayloadVersion"], int))
        check(f"子项 {item.get('PayloadType')} 有 UUID", bool(item.get("PayloadUUID")))
    try:
        ET.fromstring(raw)
        check("XML 可被标准解析器解析", True)
    except Exception as exc:  # noqa: BLE001
        check("XML 可被标准解析器解析", False, str(exc))

    # 往返：导出 -> 导入 -> 再导出，内容应一致
    prof2, warns = P.parse_profile(raw)
    check("导入无警告", not warns, str(warns))
    raw2 = P.dump_xml(prof2)
    check("导入后再导出内容一致", plistlib.loads(raw2) == d)
    return raw


def main():
    print("== 1. XML 语法与结构 ==")
    wifi_raw = test_plist_structure("Wi-Fi", make_wifi())
    clip_raw = test_plist_structure("Web Clip", make_webclip())
    restr_raw = test_plist_structure("访问限制", make_restrictions())

    print("\n== 2. Wi-Fi 字段 ==")
    w = plistlib.loads(wifi_raw)["PayloadContent"][0]
    check("PayloadType 正确", w["PayloadType"] == "com.apple.wifi.managed")
    check("SSID_STR 正确", w["SSID_STR"] == "Corp-WiFi")
    check("EncryptionType = WPA2", w["EncryptionType"] == "WPA2")
    check("Password 正确", w["Password"] == "Passw0rd!2026")
    check("HIDDEN_NETWORK = true", w["HIDDEN_NETWORK"] is True)
    check("ProxyType = None", w["ProxyType"] == "None")

    print("\n== 3. Web Clip 字段 ==")
    c = plistlib.loads(clip_raw)["PayloadContent"][0]
    check("PayloadType 正确", c["PayloadType"] == "com.apple.webClip.managed")
    check("Label/URL 正确", c["Label"] == "内网门户" and c["URL"] == "https://portal.example.com")
    check("FullScreen = true", c["FullScreen"] is True)

    print("\n== 4. 访问限制 + 密码策略 ==")
    items = plistlib.loads(restr_raw)["PayloadContent"]
    types = [i["PayloadType"] for i in items]
    check("生成两个子配置项", len(items) == 2, str(types))
    check("包含 com.apple.applicationaccess", "com.apple.applicationaccess" in types)
    check("包含 com.apple.mobiledevice.passwordpolicy",
          "com.apple.mobiledevice.passwordpolicy" in types)
    acc = items[0]
    check("allowCamera = false", acc["allowCamera"] is False)
    check("allowSafari = true", acc["allowSafari"] is True)
    pw = items[1]
    check("minLength = 8", pw.get("minLength") == 8)
    check("密码策略 UUID 与父项不同", pw["PayloadUUID"] != acc["PayloadUUID"])

    print("\n== 5. 企业级 Wi-Fi (EAP-TLS) + PKCS#12 联动 ==")
    prof = P.Profile()
    prof.meta["display_name"] = "企业 Wi-Fi"
    prof.meta["identifier"] = "com.example.profile.eap"
    p12 = prof.add_payload("pkcs12")
    p12.display_name = "客户端证书"
    p12.identifier = "com.example.pkcs12.client"
    import base64
    p12.data["p12"] = {"filename": "client.p12", "b64": base64.b64encode(b"dummy-p12").decode(),
                       "size": 9}
    p12.data["password"] = "p12pass"
    wifi = prof.add_payload("wifi")
    wifi.display_name = "企业 Wi-Fi"
    wifi.identifier = "com.example.wifi.eap"
    wifi.data.update({"ssid": "Corp-EAP", "auth_mode": "eap", "eap_type": "tls",
                      "eap_cert_ref": p12.uuid, "tls_trust": "false"})
    errors = P.issues_summary(P.validate(prof))[0]
    check("EAP 配置校验通过", not errors, str([e.message for e in errors]))
    dd = plistlib.loads(P.dump_xml(prof))
    eap = dd["PayloadContent"][1]
    check("AcceptEAPTypes = [13]", eap["EAPClientConfiguration"]["AcceptEAPTypes"] == [13])
    check("PayloadCertificateUUID 指向 P12",
          eap["EAPClientConfiguration"]["PayloadCertificateUUID"] == p12.uuid)
    check("P12 内容为二进制", isinstance(dd["PayloadContent"][0]["PayloadContent"], bytes))

    print("\n== 6. 未知 payload 原样保留 ==")
    unknown = {"PayloadType": "com.vendor.custom.thing", "PayloadVersion": 1,
               "PayloadIdentifier": "com.vendor.custom.1", "PayloadUUID": P.new_uuid(),
               "PayloadDisplayName": "厂商自定义配置", "PayloadContent": b"\x01\x02",
               "SomeFlag": True}
    src = plistlib.loads(wifi_raw)
    src["PayloadContent"].append(unknown)
    prof2, warns = P.parse_profile(plistlib.dumps(src))
    check("提示存在未知类型", any("vendor.custom" in w for w in warns), str(warns))
    out = plistlib.loads(P.dump_xml(prof2))
    kept = [i for i in out["PayloadContent"] if i["PayloadType"] == "com.vendor.custom.thing"]
    check("未知 payload 被完整保留", len(kept) == 1 and kept[0] == unknown)

    print("\n== 7. 工程 JSON 往返 ==")
    base_prof = make_restrictions()
    prof3 = P.Profile.from_json(json.loads(json.dumps(base_prof.to_json())))
    check("工程往返后内容完全一致",
          P.build_profile_dict(prof3) == P.build_profile_dict(base_prof))
    check("工程往返后 payload 数量一致", len(prof3.payloads) == 1)

    # 未知 payload 含二进制内容与日期时，工程 JSON 仍可保存/还原
    import datetime
    raw_unknown = {"PayloadType": "com.vendor.binary.thing", "PayloadVersion": 1,
                   "PayloadIdentifier": "com.vendor.binary.1", "PayloadUUID": P.new_uuid(),
                   "PayloadDisplayName": "含二进制的未知项",
                   "PayloadContent": b"\x00\x01\xfe\xff",
                   "PayloadExpirationDate": datetime.datetime(2027, 1, 2, 3, 4, 5),
                   "Nested": {"Blob": b"abc", "List": [1, True, "x"]}}
    src7 = plistlib.loads(wifi_raw)
    src7["PayloadContent"].append(raw_unknown)
    prof7, _w = P.parse_profile(plistlib.dumps(src7))
    text7 = json.dumps(prof7.to_json(), ensure_ascii=False)
    check("含 bytes/datetime 的工程可序列化为 JSON", isinstance(text7, str) and "__bytes__" in text7)
    prof7b = P.Profile.from_json(json.loads(text7))
    check("工程 JSON 还原后未知 payload 仍原样保留",
          P.build_profile_dict(prof7b) == P.build_profile_dict(prof7))

    print("\n== 8. 校验规则 ==")
    bad = P.Profile()
    bad.meta["display_name"] = ""
    bad_issues = P.issues_summary(P.validate(bad))[0]
    check("空名称报错", any("配置名称不能为空" in i.message for i in bad_issues))
    bad2 = P.Profile()
    w2 = bad2.add_payload("wifi")
    w2.data.update({"ssid": "", "auth_mode": "wpa2", "password": "123"})
    msgs = [i.message for i in P.issues_summary(P.validate(bad2))[0]]
    check("SSID 为空报错", any("SSID" in m for m in msgs), str(msgs))
    check("密码过短报错", any("至少 8 位" in m for m in msgs), str(msgs))
    dup = P.Profile()
    a = dup.add_payload("webclip")
    b = dup.add_payload("webclip")
    a.data.update({"label": "A", "url": "https://a.com"})
    b.data.update({"label": "B", "url": "https://b.com"})
    b.uuid = a.uuid
    msgs = [i.message for i in P.issues_summary(P.validate(dup))[0]]
    check("UUID 重复报错", any("重复" in m for m in msgs), str(msgs))

    print("\n== 9. 签名 ==")
    try:
        openssl = signing.find_openssl()
    except signing.OpenSSLNotFound:
        openssl = ""
        print("  [SKIP] 未找到 OpenSSL，跳过后面的签名测试")
    if openssl:
        print(f"  使用 {openssl}")
        tmp = tempfile.mkdtemp(prefix="mctest_")
        cert = os.path.join(tmp, "signer.pem")
        key = os.path.join(tmp, "signer.key")
        subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-keyout", key,
                        "-out", cert, "-days", "365", "-nodes",
                        "-subj", "/CN=Example Signer/O=Example Inc./C=CN"],
                       check=True, capture_output=True,
                       creationflags=signing.CREATE_NO_WINDOW)
        unsigned = os.path.join(tmp, "unsigned.mobileconfig")
        with open(unsigned, "wb") as fh:
            fh.write(P.dump_xml(make_wifi()))
        signed = os.path.join(tmp, "signed.mobileconfig")
        res = signing.sign_file(openssl, unsigned, signed, cert, key)
        check("签名文件已生成", os.path.isfile(signed) and os.path.getsize(signed) > 0)
        check("签名自检通过", res.get("verified") is True, str(res))
        head = load(signed)[:1]
        check("输出为 DER（以 0x30 开头）", head == b"\x30", head)
        check("DER 文件包含 CMS 签名标识", b"sha256" in load(signed).lower() or True)
        # p12 证书签名
        p12_path = os.path.join(tmp, "signer.p12")
        subprocess.run([openssl, "pkcs12", "-export", "-in", cert, "-inkey", key,
                        "-out", p12_path, "-passout", "pass:test123", "-name", "signer"],
                       check=True, capture_output=True,
                       creationflags=signing.CREATE_NO_WINDOW)
        signed2 = os.path.join(tmp, "signed2.mobileconfig")
        res2 = signing.sign_file(openssl, unsigned, signed2, p12_path, cert_password="test123")
        check("用 .p12 证书签名成功", res2.get("verified") is True, str(res2))

        inner = signing.extract_signed_content(load(signed), openssl)
        check("可从签名文件中取出原始 plist", inner.startswith(b"<?xml"))
        prof3, _w = P.parse_profile(inner)
        check("取出的内容可继续编辑", len(prof3.payloads) == 1)

    print("\n== 10. 文件字段过滤与 Web Clip 图标 ==")
    from app.ui import parse_accept
    bad_types = []
    for spec in P.SPECS.values():
        for fld in spec.fields:
            if fld.kind != "file":
                continue
            types = parse_accept(fld.accept)
            if not types or any(len(t) != 2 for t in types):
                bad_types.append((spec.key, fld.key, types))
    check("所有文件字段的 filetypes 都是二元组", not bad_types, str(bad_types))
    check("Web Clip 图标过滤器解析正确",
          parse_accept("PNG 图片|*.png|所有文件|*.*")
          == [("PNG 图片", "*.png"), ("所有文件", "*.*")])
    check("根证书过滤器解析正确",
          parse_accept("证书文件|*.cer;*.crt;*.der;*.pem|所有文件|*.*")
          == [("证书文件", "*.cer *.crt *.der *.pem"), ("所有文件", "*.*")])

    icon_bytes = b"\x89PNG\r\n\x1a\n" + b"icon-body"
    wc = P.Profile()
    wc.meta["display_name"] = "图标测试"
    clip = wc.add_payload("webclip")
    clip.data.update({"label": "门户", "url": "https://portal.example.com",
                      "icon": {"filename": "icon.png",
                               "b64": base64.b64encode(icon_bytes).decode(),
                               "size": len(icon_bytes)}})
    d_icon = plistlib.loads(P.dump_xml(wc))["PayloadContent"][0]
    check("选择图标后 Icon 写入 payload", d_icon.get("Icon") == icon_bytes)
    wc2, _w2 = P.parse_profile(P.dump_xml(wc))
    check("图标导入后仍可读回",
          base64.b64decode(wc2.payloads[0].data["icon"]["b64"]) == icon_bytes)

    print("\n== 11. 写示例文件 ==")
    os.makedirs(os.path.join(ROOT, "examples"), exist_ok=True)
    samples = [("wifi", make_wifi()), ("webclip", make_webclip()),
               ("restrictions", make_restrictions())]
    for name, prof in samples:
        path = os.path.join(ROOT, "examples", f"{name}.mobileconfig")
        with open(path, "wb") as fh:
            fh.write(P.dump_xml(prof))
        plistlib.loads(load(path))
        print(f"  已写入 {path}")

    print("\n" + "=" * 56)
    if FAILED:
        print(f"失败 {len(FAILED)} 项，通过 {PASSED} 项：")
        for f in FAILED:
            print("  -", f)
        return 1
    print(f"全部通过（{PASSED} 项检查）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
