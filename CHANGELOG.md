# 更新记录

## 未发布

### 功能

- 新增“证书信息 / 备案指纹”对话框（工具栏与“工具”菜单均可打开）：选择 Android
  `.keystore/.jks` 或 Apple `.p12/.pfx/.cer` 证书，读取 MD5 / SHA-1 / SHA-256 指纹、
  公钥（Base64）、主题、颁发者、序列号与有效期，并支持一键复制，便于 APP 备案填写
- 签名时自动处理旧式 RC2-40 加密的 p12（Windows 常见）：OpenSSL 打不开时先用系统
  .NET 转换再签名，不再需要手动重新导出证书

## 1.0.0

首个公开版本。

### 功能

- Wi-Fi（WPA2 / WPA3 / 企业级 802.1X，含 EAP-TLS 证书引用）、根证书、PKCS#12、Web Clip、访问限制 5 类 payload
- 全局字段、自动 UUID、默认标识符 `com.<组织>.<类型>.<uuid>`
- 右侧 XML 实时预览，导出前校验（必填项、UUID 唯一、`PayloadType` 合法）
- 导出 UTF-8 `.mobileconfig`；可选 OpenSSL 签名（SHA-256 / DER，支持 PEM 与 `.p12`）
- 导入已有 `.mobileconfig`（含已签名文件），未知 payload 原样保留
- 工程文件保存 / 打开（`.json`），保留证书与图标二进制
- 拖拽 `.mobileconfig` / `.json` 到窗口直接打开，非支持类型给出提示
- 一键打包单文件 exe（PyInstaller + tkinterdnd2）

### 修复

- 修复 Web Clip“图标图片”按钮点击无反应：文件过滤器的 `filetypes` 组装成了 3/4 元组，
  Tk 抛 `TclError` 且 windowed exe 无控制台输出；同一问题影响根证书与 PKCS#12 的导入按钮，
  现已统一由 `parse_accept()` 解析成合法的 `(描述, 模式)` 二元组，并补充异常提示
- 修复选择图标后未正确写入 payload / 导出 plist 缺少 `Icon` 的问题，并补充自检覆盖
- 拖拽目标注册到整棵控件树，避免拖到表单、预览等子控件上不触发
