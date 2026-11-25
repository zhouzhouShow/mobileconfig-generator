# 项目交接说明（HANDOFF）

> 给接手这个项目的会话/Trae 助手看：这个项目之前做过什么、为什么这么做、还有哪些没做完。
> 最后更新：2026-11-17

---

## 1. 这是什么

一个 **Windows 桌面小工具**，用来可视化地生成、编辑、签名 Apple 配置描述文件（`.mobileconfig`），功能对标 Apple Configurator 里的"新建/编辑配置描述文件"部分。不需要 Mac，也不需要 Xcode。

- 用户场景：给 iPhone/iPad 批量装 Wi-Fi、证书、Web Clip、访问限制这类配置
- 形态：单文件 exe，双击即用，绿色免安装
- 界面语言：简体中文

## 2. 技术栈与构建

| 项 | 选择 | 说明 |
|---|---|---|
| 语言 | Python 3.10 | |
| 界面 | Tkinter（标准库） | 三栏布局：左 payload 列表 / 中表单 / 右 XML 实时预览+校验结果 |
| plist | `plistlib`（标准库） | 不引第三方 plist 库，避免打包体积和兼容问题 |
| 拖拽 | `tkinterdnd2` | 缺失时自动降级为普通窗口，程序照常可用 |
| 签名 | 调用本机 OpenSSL | `openssl smime -sign`，SHA-256、DER、`-nodetach` |
| 打包 | PyInstaller `--onefile --windowed` | 见 `build.py` |

构建命令（在仓库根目录）：

```powershell
pip install -r requirements-dev.txt
python build.py
```

**注意：`build.py` 打包前会 `rmtree("dist")`**，dist 里如果有想保留的文件（比如使用说明）要先挪出来。

## 3. 目录结构

```text
.
├─ .github/workflows/ci.yml   # windows-latest 上跑 tests/selfcheck.py
├─ .gitignore                 # build/ dist/ *.spec *.exe __pycache__ 虚拟环境
├─ CHANGELOG.md
├─ LICENSE                    # MIT
├─ README.md                  # 中文说明（含截图、安装要点、已知限制）
├─ VERSION                    # 1.0.0
├─ build.py
├─ requirements.txt / requirements-dev.txt
├─ docs/screenshot-main.png
├─ examples/                  # 3 个示例：wifi / webclip / restrictions
├─ src/
│  ├─ main.py                 # 入口：DPI 感知 + 启动窗口 + 可选文件参数
│  └─ app/
│     ├─ __init__.py
│     ├─ profile.py           # 数据模型 / 构建 / 解析 / 校验 / 序列化
│     ├─ signing.py           # OpenSSL 多级探测 + 签名 + 提取已签名内容
│     └─ ui.py                # 界面 / 表单 / 预览 / 拖拽
└─ tests/
   ├─ selfcheck.py            # 无界面自检（77 项）
   └─ ui_check.py             # 界面与拖拽逻辑检查（8 项）
```

## 4. 已实现的功能

**5 类 payload**（只支持这 5 类，其它类型只能原样保留）：

1. **Wi-Fi**：WPA2 / WPA3 / WPA / WEP / 开放；企业级 802.1X（PEAP / TTLS / EAP-TLS）；代理支持手动与 PAC
2. **根证书**：`.cer/.crt/.pem` 自动转 DER
3. **PKCS#12**：证书+私钥打包进 payload
4. **Web Clip**：图标可选图片文件，写入 `Icon` 为 PNG 原始字节
5. **访问限制**：16 项功能限制开关 + 可选密码策略（自动生成 `com.apple.mobiledevice.passwordpolicy` 子项）

**全局与流程功能**：

- 全局字段齐全（`PayloadVersion` 整数 1、`PayloadContent` 数组、`ConsentText` 以 dict default 形式、`PayloadExpirationDate` 为 plist date）
- 校验：必填项、UUID 唯一性、标识符合法性、EAP 证书引用
- **导入** `.mobileconfig`（含已签名文件，用 OpenSSL 提取内容）；未知 payload 原样保留
- **工程文件**（`.json`）保存/打开，bytes 与 datetime 安全转换
- 导出 UTF-8 XML；OpenSSL 签名（PEM 与 p12 均可）
- **拖拽**：拖入 `.mobileconfig` 直接加载、拖入 `.json` 打开工程、其它类型提示且不改动当前内容、有未保存修改先走保存确认
- 命令行带文件参数启动可直开

## 5. 已修过的坑（别重复踩）

| 现象 | 根因 | 修法 |
|---|---|---|
| Web Clip「图标图片」「证书文件」按钮点了没反应 | `filetypes` 拼成了 4 元组/3 元组，Tk 只接受 `(描述, 模式)` 二元组，`askopenfilename` 抛 `TclError`；windowed exe 无控制台，异常被吞掉 | 新增 `parse_accept()` 正确配对，对话框调用加异常兜底弹窗 |
| 拖拽只有拖到窗口边缘才生效 | OLE 按鼠标下窗口判定落点，只注册根窗口时子控件不接收 | 启动时遍历整棵控件树逐控件注册 `DND_FILES`，表单重建后补注册（按 Tk 路径 + 对象身份去重） |
| 每次重跑自检示例 UUID 都变，文档里的 XML 对不上 | 示例生成用了随机 UUID | 示例改为固定 UUID |
| 打包后拖拽失效 | `tkinterdnd2` 的 tcl/dll 没进包 | `build.py` 加 `--hidden-import` + `--collect-all tkinterdnd2` |

## 6. 验证方式

```powershell
python tests\selfcheck.py    # 77 项，全部通过
python tests\ui_check.py     # 8 项，界面与拖拽逻辑
```

selfcheck 覆盖：XML 结构（`PayloadVersion` 整数、`PayloadContent` 数组、各 PayloadType 正确）、导出→导入→再导出一致、未知 payload 保留、EAP-TLS+P12 联动、工程 JSON 往返、校验规则、OpenSSL 签名（DER 以 0x30 开头、自检通过、p12 签名、从签名文件回读）、文件过滤器解析、Web Clip 图标写入/读回。

ui_check 覆盖：图标数据写回、导出 plist 的 `Icon` 为原始 PNG 字节、`tkinterdnd2` 加载、控件树 >40 且全部注册拖放目标、拖入 `.mobileconfig` 成功加载、非法类型提示且不改内容。

**未能自动化的一项**：真实鼠标拖拽（本机合成鼠标输入驱动不了 OLE，连 WinForms 目标也收不到落点）。拖放通道已在 OS 层验证 79/79 个子窗口注册成功，但仍需人工拖一次确认。

## 7. 仓库与发布状态

- 已 `git init`，分支 `main`，工作区干净，MIT 协议，带 `ci.yml`
- 已推送到 GitHub：<https://github.com/zhouzhouShow/mobileconfig-generator>（public）
- 本机直连 `github.com:443` 不通，push 需走代理：`git -c http.proxy=http://127.0.0.1:10808 push`
- `dist/` 不入库：exe 与使用说明文档都不在仓库里，需要时重新 `python build.py` 生成
- 本地证书目录 `签名文件/` 已在 `.gitignore` 中忽略（内含签名私钥，严禁入库）
- 硬编码检查已做：全仓无本机绝对路径/用户名/临时目录，OpenSSL 路径全部由 `shutil.which` 与环境变量拼接

推送方式：

```powershell
git remote add origin https://github.com/<账号>/<仓库名>.git
git push -u origin main
# 或
gh repo create <仓库名> --public --source=. --remote=origin --push
```

## 8. 未完成 / 待决策

1. **签名缺证书链（最优先，用户当前卡点）**
   现在签名只从 p12 里取叶子证书，iOS 收到后仍显示"未签名"。原因基本确定是 CMS 里没带中间证书（`Apple Worldwide Developer Relations Certification Authority`），iOS 不会自动补链。
   待做：加一个可选的"中间证书/证书链"文件输入，签名时以 `-certfile` 补进 CMS；签名成功后显示实际带了几张证书、链是否完整。

2. **使用说明.md 丢失**：原 `dist\使用说明.md` 已被环境临时目录清理移走，如需交付要按 README 重写一份（注意先还原再打包，否则被 `rmtree` 清掉）。

3. **拖拽人工验收**：见第 6 节。

4. 已知能力边界：只覆盖 5 类 payload；导入已签名文件不保留签名，需重新签。

## 9. 关于 iOS 签名的重要结论（研究过，别再重复查）

- **不需要专门为描述文件申请"签名证书"**。Apple 官方对签名证书的 EKU 没有要求，自签证书/私有 CA/TLS 证书/代码签名证书都能用，前提是设备信任其证书链。
- 用 **Apple Distribution 证书签本身没问题**，但必须把 WWDR 中间证书一起签进 CMS，否则 iOS 判为未签名。网上流传的"必须带 Email Protection EKU"是误传，无 Apple 出处。
- **不签名也能安装**，只是显示"未签名"，功能不打折。官方口径：只有"手动下载安装"的 profile 才需要签，MDM 下发不需要。
- 签名必须是 **DER 编码的 CMS SignedData，内容内嵌（`-nodetach`），SHA-256**。漏 `-nodetach` 就是分离式签名，等于没签。
- **已签名的 profile 替换时必须是同一签名身份**，否则设备拒绝覆盖（MDM 下发除外）。
- 自查命令：

```bash
openssl pkcs7 -inform DER -in signed.mobileconfig -print_certs -text -noout   # 看带了几张证书
openssl cms -verify -inform DER -in signed.mobileconfig -CAfile root.pem -purpose any
```

## 10. 背景研究（不属于本仓库，但用户问过）

用户当时在解决"uni-app 打的 iOS 包要上传 TestFlight，但没有 Mac"的问题，结论已落成两份文档：

- GitHub Actions macOS runner 实操手册（镜像版本、计费、签名链路、最小 workflow、常见坑）
- uniapp-ios-testflight-plan.md（`npm run build:app` 只产前端资源不出 ipa；ipa 仍需 HBuilderX 云打包；上传环节才绑定 macOS，可用 `macos-26` runner 只跑上传，单次约 2–5 分钟）

关键提醒：**ipa 签名后无法再改 build number**，云打包前必须在 `manifest.json` 里自增 `version.code`，否则上传被 Apple 以构建号重复拒绝。

## 11. 用户偏好与协作方式

- 中文沟通，喜欢直接给结论 + 可执行步骤，不要长篇铺垫
- 提需求偏功能性、口语化，需要主动确认边界（比如"哪种包""什么用途"）
- 对界面细节敏感（按钮点了没反应这类问题会马上反馈）
