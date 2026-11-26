# 更新记录

## 1.1.4

### 修复

- 应用内更新长时间停在“下载中”：下载改为显示已下载 MB / 总大小与百分比；
  连接超时缩短到 20 秒并给出明确提示，关闭更新窗口即取消下载并清理临时文件
- 更新下载失败时区分 404、网络超时等情况，并提示改用「打开发布页」手动下载

## 1.1.3

### 修复

- 修复 Win+D 或最小化后窗口偶尔无法恢复的问题：窗口重新出现时校正屏幕位置、提升到前台，
  并恢复被一起隐藏的子弹窗
- 主窗口尺寸按屏幕可用区域自适应，避免在大分辨率缩放或小屏上大于桌面
- Release 资产改为英文名 `AppleProfileTool.exe`（GitHub 会丢掉非 ASCII 资产名，此前被截成 `Apple.exe`）

## 1.1.2

### 修复

- 修正 GitHub Actions 产出的 exe 文件名被截成 `Apple.exe` 的问题（打包时先用 ASCII 名，完成后再改成中文名）

## 1.1.1

### 修复

- GitHub Actions 在 Windows runner 上因控制台编码（cp1252）无法打印中文而失败，自检脚本与工作流统一使用 UTF-8
- 上传失败提示补充日志末尾原样输出，并细化 API 密钥类型（团队 / 个人）相关提示

## 未发布

### 界面

- 整体重做视觉：浅色纸面 + 单一强调蓝，白色卡片式分组、细边框输入框、深色 XML 代码预览区
- 顶部改为品牌栏 + 分组工具栏，主操作「签名并导出」使用实心强调色按钮
- 签名、证书信息、使用说明三个对话框统一新样式
- 顺带修复配置项 UUID 输入框在垃圾回收后显示为空的问题

### 功能

- 新增「上传 ipa」：调用 Apple 官方免费的 Windows 版 iTMSTransporter，
  使用 App Store Connect API Key 上传构建，无需 Mac
- 上传支持两种认证：App Store Connect API Key，或 Apple ID + 应用专用密码
- iTMSTransporter 自动检测覆盖注册表安装记录与自定义盘符目录，也可手动选择安装目录
- 新增「检查更新」：读取 GitHub Releases，发现新版本可一键下载并自动替换重启
- 帮助菜单可关闭“启动时自动检查更新”；推送 `v*` 标签即由 GitHub Actions 自动打包发布
- 自动解析 ipa 的 Bundle ID、版本号、Build 号与最低系统版本
- 上传日志实时显示，常见错误（密钥无效、构建号重复、Bundle ID 不存在）自动翻译
- 支持“复制命令”，排障时可在命令提示符中手动执行


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
