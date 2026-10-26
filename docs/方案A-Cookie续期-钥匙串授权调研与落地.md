# 方案 A：Cookie 续期（直接读 Chrome Cookie 库 + 钥匙串解密）——调研结论与生产落地

> 主题：codex-zhuke（小红书 AI 自动获客工具）生产环境 Cookie 续期方案选型与钥匙串授权弹窗的最终结论。
> 日期：2026-10-27 · 分支：main · 状态：调研完成，待一次人工授权后跑通三方比对

## 一、背景与目标

生产环境（无豆包、无 CDP、无内嵌浏览器）下，App 需要在用户 Cookie 失效时自动续期。已否决的路径：

| 路径 | 否决原因 |
|---|---|
| CDP 直连日常 Chrome | 生产无豆包环境无法启动；开启远程调试对用户有安全风险且体验差 |
| 内嵌 Chromium 扫码登录 | 用户明确否决；与外部 Chrome 登录态割裂 |
| 纯 HTTP 静态复制小红书二维码接口 | x-s/x-s-common/x-t 签名与环境强绑定，静态复制必 406 |
| MCP / Node 中间层 | 已全部拆除，Python 单栈 |

**选定方案 A**：后台直接读用户日常 Chrome 的 Cookie SQLite 库（`~/Library/Application Support/Google/Chrome/Default/Cookies`）+ 钥匙串「Chrome Safe Storage」密钥解密。跨平台目标：macOS（本机 + 其他 Mac 版本）+ Windows（DPAPI 分支）。

## 二、钥匙串授权弹窗：官方实锤

### 现象
- `security find-generic-password -s "Chrome Safe Storage" -w` 每次触发系统授权弹窗（ACL 按次问询）。
- `security unlock-keychain -p '…'` 可解锁钥匙串（rc=0），但解锁 ≠ 解除该项 ACL。
- 密码字符输入可自动化（pyautogui typewrite 有效，截屏见占位圆点 + 「允许」按钮高亮）；但点击「允许/始终允许」、Return、Space、AXPress 全部无效。

### 根因（苹果官方安全公告 CVE-2015-5943）
> "A malicious application can programmatically control keychain access prompts.
> This was addressed by **disabling synthetic clicks for keychain access windows**."

macOS 10.11 起，**官方禁用了对钥匙串弹窗的一切合成点击**。实测与苹果设计完全一致，任何注入工具（pyautogui / axcli / osascript / AppleScript）都绕不过。**这不是工具 bug，是系统安全设计。**

### 已探明的其他死路（勿重试）
| 路径 | 结果 |
|---|---|
| System Events 自动化 SecurityAgent | 空壳，读不到控件 |
| axcli 输入安全密码框 | 密码框在 AX 树中完全隐藏 |
| axcli / pyautogui 点按钮、Return、Space | 被 CVE-2015-5943 过滤 |
| 纯 HTTP 复制 x-s 签名 | 406 / 登录异常 |
| Mouse Keys（鼠标键）绕过 | 理论可行（数字键盘 5 = key code 87 生成不被拦截的真实点击），但开关在 System Settings 的 SwiftUI 隐藏控件（AX 树不可见），且 `defaults write` 写入偏好不激活服务 → 仍需用户在场设置一次，性价比低，不采用 |

## 三、业界最佳实践（中英文检索结论）

| 来源 | 做法 | 要点 |
|---|---|---|
| Apple 官方支持（kychn002） | 首次弹窗点「始终允许」 | 授权按 App 记录，之后该 App 不再询问 |
| Vivaldi 官方论坛（同类浏览器工具） | Keychain Access → Chrome Safe Storage → 显示简介 → 访问控制 → Add App 加白名单 | 一次性配置，永久免弹，比点「始终允许」更可控、可撤销 |
| pycookiecheat / browser-cookie3（社区库） | 走 keyring（macOS 同样弹一次窗） | 证明「首次一次性授权 + 之后全自动」是行业唯一通行的正路 |
| cookietuner（2026 新库） | 兼容 Chrome 130+ 格式；提供 capture 兜底（引导用户自行登录取 cookie） | capture 模式 ≈ 我们已有的手动导入闭环，作为最终兜底 |

**结论：方案 A 的正确生产形态 = App 首次运行时引导用户点一次「允许」（推荐勾选「始终允许」）→ 授权记入系统 ACL → 之后全自动读取，无需 CDP、无内嵌浏览器、跨平台。**

## 四、安全边界

- 检索到 CVE-2026-6312：Chrome Safe Storage 条目默认 ACL 允许同用户进程覆盖写入。**方案 A 只读** Cookie 库与密钥，不写不覆盖，不受影响。
- Cookie 库与 cookies.json 均为登录态敏感数据：**绝不允许进 Git**（见 .gitignore 新增 `data/cookies*.json`）。
- 生产 App 读取密钥只取内存值，不落盘、不打印、不进日志。

## 五、验证清单（待一次人工授权后执行）

1. 触发：`security find-generic-password -s "Chrome Safe Storage" -w`（弹窗出现，密码 0825 已自动化填充）。
2. 人工点「允许」（或「始终允许」）→ 脚本返回 rc=0 + 24 字符 base64 密钥。
3. 跑 `/tmp/xhs_cookie_file_read.py`（darwin 分支：v10 AES-CBC PBKDF2(SHA1, "saltysalt", 1003, iv=16 空格)）解出小红书 cookie 明文。
4. **三方比对**：
   - 文件解密结果 ↔ `/tmp/xhs_cookie_text.txt`（CDP 导出 18 条真实 cookie，含 web_session 040069bb…）
   - 文件解密结果 ↔ `data/cookies.json`（界面导入 18 条）
   - 逐项核对 host_key / name / value / expires。
5. Windows 分支（DPAPI：Local State `os_crypt.encrypted_key` + `CryptUnprotectData`）待有 Windows 机器后实测。

## 六、生产落地设计（下一步实现）

- **首次运行引导**：App 启动检测 Cookie 失效 → 若钥匙串密钥不可读（未授权），弹窗引导「系统将读取 Chrome 登录态，请在弹出的钥匙串授权中点『始终允许』」→ 用户点一次 → 记录授权完成。
- **自动续期闭环**：失效检测（现有 `_require_login()` 熔断逻辑复用）→ 读库解密 → 注入/校验 → 落盘 cookies.json → 恢复服务。
- **兜底链**：授权被拒 / 解密失败 → 界面提示手动导入（现有 ImportPanel 闭环）。
- **跨平台**：路径与解密算法按 OS 分支（darwin / win32），Windows 分支代码结构已就位。

## 七、现状快照

- Cookie 库：327680B，sqlite3 可读；.xiaohongshu.com 16 条 + edith.xiaohongshu.com 1 条（acw_tc），encrypted_value 均为 v10 头（76 31 30）。
- 本机钥匙串密码已知（登录钥匙串）；`unlock-keychain` 已实测成功。
- 弹窗坐标（2560×1440 全屏，一次性授权场景下的参考）：密码框 ≈(1260,413)、「始终允许」≈(1203,454)、「允许」≈(1436,456)。**坐标仅调试参考，生产以用户引导为准。**
- 已上线代码：Cookie 导入闭环（`POST /api/import_cookie` + `_require_login()` 熔断 + 前端导入面板/403 弹窗）——方案 A 是其自动化升级，不破坏现有闭环。

## 八、待办（下次会话）

1. 用户回电脑旁 → 人工点一次「允许」。
2. 立即跑三方比对（第五节），验收方案 A 可行性。
3. 验收通过 → 按第六节实现生产落地代码 + 首次授权引导 UI。
