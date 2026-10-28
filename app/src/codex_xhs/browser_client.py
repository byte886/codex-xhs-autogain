"""Playwright 浏览器执行器（async）—— 后端浏览器控制统一为 Python 单栈。

设计：
- 与 uvicorn 同事件循环：全部 async API，连接与操作都在 asyncio 事件循环内完成，
  彻底规避 sync API 在 FastAPI 线程池下的 greenlet 跨线程崩溃（已实测复现后根治）。
- 同进程常驻：首次调用连接 Chrome 一次，此后 search/note/cookie/login 全部复用同一连接。
- 连接方式可插拔（生产不依赖豆包/调试通道）：
    XHS_BROWSER_MODE=daily    默认：连日常 Chrome（复用已扫码登录态；首次连接弹一次授权窗，自动代点）
    XHS_BROWSER_MODE=profile  交付：APP 自拉独立 profile 真实 Chrome（无授权弹窗；需客户扫码一次）
- DOM 提取复用 extension/lib/extract.js（页面内 evaluate 注入，单一提取源）。
"""
import asyncio
import json
import os
import pathlib
import sys
import re
import subprocess
import time

from playwright.async_api import async_playwright

_ROOT = pathlib.Path(__file__).resolve().parents[3]  # app/src/codex_xhs -> 项目根
_EXTRACT_SRC = (_ROOT / "extension" / "lib" / "extract.js").read_text(encoding="utf8")
_PRESS_SCRIPT = _ROOT / "scripts" / "browser" / "cdp" / "press_allow.applescript"
_XHS = "https://www.xiaohongshu.com"

_pw = None
_browser = None
_connect_lock: asyncio.Lock | None = None
_press_lock = asyncio.Lock()  # 授权代点进程内串行（async 侧同事件循环，天然串行）
_fail_until = 0.0


def _ws_endpoint() -> str:
    """直连常驻生产 Chrome（9223 独立 profile）。connect_over_cdp 支持 http 端点，
    自动通过 /json/version 解析 WebSocket 地址，不再依赖日常 Chrome 的 DevToolsActivePort。
    """
    port = int(os.environ.get("XHS_PROFILE_PORT", "9223"))
    return f"http://127.0.0.1:{port}"


async def _press_consent_once(restore_to: str = "") -> bool:
    """点一次授权弹窗（子进程调 AppleScript）。async 侧用 asyncio.Lock 串行，
    避免并发 osascript 拥塞 System Events（文件锁版本已实测自锁弃用）。

    可移植性：仅 macOS（Darwin）+ 脚本存在时才代点；其他平台/缺脚本时静默
    返回 False，授权弹窗由用户手动点「允许」（连接本身不受影响）。
    """
    async with _press_lock:
        if sys.platform != "darwin" or not _PRESS_SCRIPT.exists():
            return False  # 非 macOS 或无脚本：授权窗由用户手动确认
        try:
            r = await asyncio.to_thread(
                subprocess.run,
                ["osascript", str(_PRESS_SCRIPT), restore_to],
                capture_output=True, timeout=5,
            )
            return b"pressed=true" in r.stdout
        except Exception:  # noqa: BLE001
            return False


async def _connect_daily_with_consent():
    """连接日常 Chrome；连接握手期间并发代点授权弹窗（Chrome 144+ 每次连接必弹，官方无法关闭）。"""
    global _pw
    endpoint = _ws_endpoint()

    async def presser():
        while True:
            await _press_consent_once()
            await asyncio.sleep(0.6)

    task = asyncio.create_task(presser())
    try:
        _pw = await async_playwright().start()
        return await _pw.chromium.connect_over_cdp(endpoint, timeout=20000)
    finally:
        task.cancel()


async def get_browser():
    """常驻连接（async 锁保护）。连接失败冷却 20s 后自动重试。"""
    global _browser, _fail_until, _connect_lock
    if _connect_lock is None:
        _connect_lock = asyncio.Lock()
    async with _connect_lock:
        if _browser is not None and _browser.is_connected():
            return _browser
        now = time.monotonic()
        if _fail_until > now:
            raise RuntimeError(f"浏览器连接冷却中（{int(_fail_until - now)}s 后再试）")
        try:
            if os.environ.get("XHS_BROWSER_MODE") == "profile":
                _browser = await _connect_profile()
                await _inject_saved_cookies()  # 重启后端后自动恢复登录态（幂等注入 cookies.json）
            else:
                _browser = await _connect_daily_with_consent()
            return _browser
        except Exception as e:  # noqa: BLE001
            _fail_until = time.monotonic() + 20
            raise RuntimeError(f"连接浏览器失败: {e}") from e


async def _inject_saved_cookies():
    """profile 模式：把已保存的 cookies.json 自动注入独立浏览器（幂等）。

    背景（实测发现）：重启后端后 _browser 缓存重置、重新连接 9223 独立 Chrome，
    之前 verify 时注入的 Cookie 会丢（登录态变 unknown/not_logged_in，熔断误伤）。
    每次 profile 连接成功后覆盖注入 cookies.json：Cookie 相同则无害，失败静默。
    注意：本函数只在 get_browser() 的 _connect_lock 内被调用，直接使用模块级 _browser；
    严禁在此处再次调用 get_browser()（asyncio.Lock 不可重入，会死锁挂起）。
    """
    try:
        p = _ROOT / "data" / "cookies.json"
        if not p.exists():
            return False
        data = json.loads(p.read_text(encoding="utf8"))
        ck = data.get("cookies", [])
        if not ck:
            return False
        await _browser.contexts[0].add_cookies(ck)
        return True
    except Exception:  # noqa: BLE001
        return False


_CDP_CONFIG = _ROOT / "data" / "cdp_config.json"


def _home_relative(p: str) -> str:
    """把 $HOME 下的绝对路径规范化为 ~ 相对形式（可移植：换用户/换机不失效）。

    仅转换 $HOME 前缀（含整 HOME 本身）；其余绝对路径（如 /Applications/...）保持原样。
    """
    if not p:
        return p
    home = os.path.expanduser("~")
    try:
        if p == home:
            return "~"
        if p.startswith(home + os.sep):
            return "~" + p[len(home):]
    except Exception:  # noqa: BLE001
        pass
    return p


def _load_cdp_config() -> dict:
    """读取用户自定义的 Chrome 路径 / Profile 路径配置（无则空）。"""
    try:
        if _CDP_CONFIG.exists():
            return json.loads(_CDP_CONFIG.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {}


def save_cdp_config(chrome_path: str = "", profile_dir: str = "") -> dict:
    """保存用户自定义 Chrome 路径 / Profile 路径（持久化，跨会话保留）。

    路径先经 _home_relative 规范化为 ~ 相对形式，保证跨用户/跨机可移植
    （读取侧统一 expanduser 展开）。
    """
    cfg = _load_cdp_config()
    if chrome_path:
        cfg["chrome_path"] = _home_relative(chrome_path)
    if profile_dir:
        cfg["profile_dir"] = _home_relative(profile_dir)
    _CDP_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    _CDP_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def chrome_executable() -> str | None:
    """跨平台定位 Google Chrome 可执行文件：用户自定义优先，其次按平台最佳实践探测。

    macOS：/Applications 默认 + ~/Applications 用户级安装。
    Windows：注册表 App Paths（HKCU→HKLM→WOW6432Node，即运行对话框使用的权威路径）
    → 卸载注册表 → 常见安装路径（%LOCALAPPDATA% / Program Files / Program Files (x86)）。
    """
    user_path = _load_cdp_config().get("chrome_path")
    if user_path:
        user_path = os.path.expanduser(user_path)
        if os.path.exists(user_path):
            return user_path
    if sys.platform == "darwin":
        return _mac_chrome_candidates()
    if os.name == "nt":
        return _first_existing(_win_chrome_candidates())
    return None


def _mac_chrome_candidates() -> str | None:
    """macOS Chrome 定位候选：/Applications 默认安装 + ~/Applications 用户级安装。"""
    candidates = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        str(pathlib.Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    )
    return _first_existing(list(candidates))


def _first_existing(paths: list[str]) -> str | None:
    """返回候选路径列表中第一个真实存在的路径，均不存在时返回 None。"""
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def _win_chrome_candidates() -> list[str]:
    """Windows Chrome 定位候选（最佳实践顺序）：注册表 → 常见安装路径。"""
    paths: list[str] = []
    try:
        import winreg
        for hive, sub in [
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"),
        ]:
            try:
                with winreg.OpenKey(hive, sub) as k:
                    val, _ = winreg.QueryValueEx(k, "")
                    if val:
                        paths.append(str(val))
            except OSError:  # noqa: PERF203
                continue
    except ImportError:
        pass
    paths += [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    return paths


def _profile_settings() -> tuple[int, str]:
    """CDP 端口与独立 profile 目录（交付模式：不污染用户日常登录态）。

    用户可在前端 CDP 面板自定义 Profile 路径（持久化到 data/cdp_config.json），
    优先读用户配置，其次环境变量，最后默认值（~/.codex-xhs-profile）。
    """
    port = int(os.environ.get("XHS_PROFILE_PORT", "9223"))
    cfg = _load_cdp_config()
    profile_dir = os.path.expanduser(
        cfg.get("profile_dir") or os.environ.get("XHS_PROFILE_DIR")
        or os.path.join(os.path.expanduser("~"), ".codex-xhs-profile")
    )
    return port, profile_dir


def chrome_running() -> bool:
    """检测 Chrome 是否在运行（跨平台）。"""
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
                                 capture_output=True, text=True, timeout=10).stdout
            return "chrome.exe" in out
        return subprocess.run(["pgrep", "-x", "Google Chrome"], capture_output=True,
                              timeout=5).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def quit_chrome() -> bool:
    """退出 Chrome（macOS osascript；Windows taskkill）。返回是否发起。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/IM", "chrome.exe", "/F"],
                           capture_output=True, timeout=10)
            return True
        r = subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to quit'],
                           capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def launch_profile_chrome() -> str | None:
    """自动拉起带调试参数的独立 profile Chrome。返回 Chrome 可执行路径（找不到返回 None）。

    备选方案（启动参数方式）：主推 Chrome 144+ 的 chrome://inspect 远程调试开关（无需参数），
    本函数仅在引导失败 / Chrome <144 时兜底。"""
    chrome = chrome_executable()
    if not chrome:
        return None
    port, profile_dir = _profile_settings()
    os.makedirs(profile_dir, exist_ok=True)
    win_args = [] if os.environ.get("XHS_PROFILE_WINDOW") == "normal" else ["--start-minimized"]
    subprocess.Popen(
        [chrome, f"--user-data-dir={profile_dir}", f"--remote-debugging-port={port}",
         "--no-first-run", "--no-default-browser-check", "--new-window", *win_args],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return chrome


def open_inspect_remote_debugging() -> str | None:
    """引导用户开启远程调试：用 Chrome 打开 chrome://inspect/#remote-debugging（Chrome 144+ 官方方式，无需启动参数）。

    返回 Chrome 可执行路径（找不到返回 None）。由用户在此页面开启「Allow remote debugging」。"""
    chrome = chrome_executable()
    if not chrome:
        return None
    subprocess.Popen([chrome, "chrome://inspect/#remote-debugging"])
    return chrome


async def _connect_profile():
    """交付模式：APP 自拉独立 profile Chrome（无授权弹窗），连接失败时自动拉起。"""
    global _pw
    port, profile_dir = _profile_settings()
    if _pw is None:
        _pw = await async_playwright().start()
    try:
        return await _pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=8000)
    except Exception:
        launch_profile_chrome()
        for _ in range(30):
            await asyncio.sleep(1)
            try:
                return await _pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=5000)
            except Exception:  # noqa: BLE001
                continue
        raise RuntimeError("独立 Chrome 调试端口未就绪")


async def _page():
    """取一个可用的页面 target（后台新建，不抢用户前台焦点；失败退化为前台新页）。

    后台 tab 会被 Chrome 渲染节流（SPA 永远不渲染 → 采集 0 条），因此建 tab 后
    立即用 Target.activateTarget 激活再交给调用方 goto；调用方 page.close() 后
    焦点自然归还原 tab。
    """
    ctx = (await get_browser()).contexts[0]
    try:
        src = ctx.pages[0]
        cdp = await ctx.new_cdp_session(src)
        resp = await cdp.send("Target.createTarget", {"url": "about:blank", "background": True})
        try:
            await cdp.send("Target.activateTarget", {"targetId": resp["targetId"]})
        except Exception:  # noqa: BLE001
            pass
        for _ in range(50):
            for p in ctx.pages:
                if p.url == "about:blank" and len(ctx.pages) > 1 and p != src:
                    return p
            await asyncio.sleep(0.2)
    except Exception:  # noqa: BLE001
        pass
    return await ctx.new_page()


async def _close_page_safe(page):
    """关闭页面但保证浏览器至少留一个空白页。

    关键（风控/反复开关根因）：Chrome 关闭最后一个页面时整个浏览器会退出，
    下次任务检测不到 9223 又自动拉起 → 反复开关 Chrome/Tab，观感极不拟人。
    因此在关闭前若只剩本页，先补开一个 about:blank 保底，浏览器保持常驻。
    """
    try:
        ctx = page.context
        if len(ctx.pages) <= 1:
            await ctx.new_page()  # about:blank 保底
    except Exception:  # noqa: BLE001
        pass
    try:
        await page.close()
    except Exception:  # noqa: BLE001
        pass


async def cleanup_tabs():
    """清理 9223 生产 Chrome 中与任务无关的标签页：APP 前端页(127.0.0.1:8791)与
    about:blank 保留原样，其余全部关闭。避免误关用户正在使用的 APP 窗口。

    使用场景：测试/演示前后、以及长时间运行后定期执行，避免反复 _page() 建后台页造成 tab 堆积。
    对生产会话安全：只关多余页面、不动浏览器本身；连接复用 get_browser() 现有连接。
    清理后若页面清空，补开一个 about:blank 保底（避免 Chrome 因无页面退出）。
    """
    try:
        browser = await get_browser()
        ctx = browser.contexts[0]
        pages = ctx.pages
        closed = 0
        for p in pages:
            u = p.url or ""
            if "127.0.0.1:8791" in u or u == "about:blank":
                continue  # 用户 APP 页/空白页保留，绝不归位或关闭
            try:
                await p.close()
                closed += 1
            except Exception:  # noqa: BLE001
                continue
        if not ctx.pages:  # 全部清空 → 补空白页保底，浏览器不退出
            try:
                await ctx.new_page()
            except Exception:  # noqa: BLE001
                pass
        return closed
    except Exception as exc:  # noqa: BLE001
        print(f"[cleanup_tabs] 清理失败（非阻断）: {exc}")
        return 0


async def _load(url: str, wait_ms: int = 5000):
    page = await _page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=40000)
        # 后台建 tab 会被 Chrome 渲染节流（SPA 永不渲染 → 采集 0 条）。
        # 激活页面触发真实渲染；采集/克隆结束 page.close() 后焦点自然归还原 tab。
        try:
            await page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass
        await page.evaluate(_EXTRACT_SRC)
        await asyncio.sleep(wait_ms / 1000)
        return page
    except Exception:
        try:
            await _close_page_safe(page)
        except Exception:  # noqa: BLE001
            pass
        raise


async def login_check() -> dict:
    """登录态检测：优先走无界面 headless 判定（不产生窗口/Dock 图标），失败回退常驻浏览器。"""
    try:
        return await _login_check_headless()
    except Exception as e:  # noqa: BLE001
        print(f"[login_check] headless 判定失败，回退浏览器: {e}")
        page = await _load(_XHS + "/explore", 6000)
        try:
            return await page.evaluate(
                "() => ({"
                " logged_in: !!xhsIsLoggedIn(),"
                " url: location.href,"
                " hasLoginUi: /website-login|captcha/i.test(location.href),"
                " noteCards: xhsExtractSearchNotes().length"
                "})"
            )
        finally:
            await _close_page_safe(page)


_h_pw = None
_h_browser = None


async def _headless_page():
    """无界面 Chromium（channel=chrome 无头模式）：不产生窗口、不进 Dock。
    仅承担登录判定/校验，不承担采集（采集走常驻 9223）。"""
    global _h_pw, _h_browser
    if _h_pw is None:
        _h_pw = await async_playwright().start()
    if _h_browser is None or not _h_browser.is_connected():
        _h_browser = await _h_pw.chromium.launch(channel="chrome", headless=True)
    return _h_browser


async def _headless_explore(cookies: list[dict]) -> dict:
    """在无界面 Chromium 中：清 Cookie → 注入指定 Cookie → 打开小红书首页判定登录态。

    headless 下 explore 无 cookie 时会发生 SPA 重定向（evaluate 注入的函数会随旧文档丢失），
    因此用 add_init_script 在【每次文档加载前】自动注入提取脚本，判定时全局函数必然存在。
    """
    b = await _headless_page()
    ctx = b.contexts[0] if b.contexts else await b.new_context()
    try:
        await ctx.clear_cookies()
        await ctx.add_init_script(_EXTRACT_SRC)
        if cookies:
            try:
                await ctx.add_cookies(cookies)
            except Exception:  # noqa: BLE001
                pass
        page = await ctx.new_page()
        try:
            await page.goto(_XHS + "/explore", wait_until="domcontentloaded", timeout=40000)
            await asyncio.sleep(5)
            return await page.evaluate(
                "() => ({"
                " logged_in: !!xhsIsLoggedIn(),"
                " url: location.href,"
                " hasLoginUi: /website-login|captcha/i.test(location.href),"
                " noteCards: xhsExtractSearchNotes().length"
                "})"
            )
        finally:
            await _close_page_safe(page)
    finally:
        # 判定完成后移除注入脚本，避免污染后续 context 复用
        try:
            await ctx.remove_init_script(_EXTRACT_SRC)
        except Exception:  # noqa: BLE001
            pass


async def _login_check_headless() -> dict:
    """headless 登录判定：自动带上已保存 cookies.json（生产登录态），无浏览器窗口。"""
    ck: list = []
    try:
        p = _ROOT / "data" / "cookies.json"
        if p.exists():
            data = json.loads(p.read_text(encoding="utf8"))
            ck = data.get("cookies", []) or []
    except Exception:  # noqa: BLE001
        ck = []
    return await _headless_explore(ck)


async def cookies() -> dict:
    page = await _load(_XHS + "/explore", 3000)
    try:
        cdp = await page.context.new_cdp_session(page)
        data = await cdp.send("Network.getAllCookies")
        xhs = [c for c in data.get("cookies", []) if "xiaohongshu" in c.get("domain", "") or "xhscdn" in c.get("domain", "")]
        ws = next((c.get("value", "") for c in xhs if c.get("name") == "web_session"), "")
        return {
            "count": len(xhs),
            "web_session": ws,
            "names": [c.get("name") for c in xhs],
            "top": [
                f"{c.get('domain')} {c.get('name')}={(c.get('value') or '')[:10]}…"
                for c in xhs[:15]
            ],
        }
    finally:
        await _close_page_safe(page)


async def verify_cookies(cookies: list[dict]) -> dict:
    """校验导入/读取的 Cookie：优先无界面 headless 判定（不弹窗口），失败回退独立 profile Chrome。

    headless 判定说明：无头 Chromium 中清 Cookie → 只注入待校验 Cookie → 访问小红书首页判定登录态；
    不产生任何窗口/Dock 图标（用户「只用一个 Chrome」诉求）。若 headless 判定结果异常（页面未加载、
    url 为空等）则回退 9223 常驻浏览器执行原校验逻辑。
    """
    try:
        r = await _headless_explore(cookies)
        if not r.get("url") or not isinstance(r.get("hasLoginUi"), bool):
            raise RuntimeError("headless 判定结果异常")
        return r
    except Exception as e:  # noqa: BLE001
        print(f"[verify_cookies] headless 校验失败，回退 9223: {e}")
        return await _verify_profile(cookies)


async def _verify_profile(cookies: list[dict]) -> dict:
    """（回退路径）在独立 profile Chrome 中【先清空已有 Cookie】→
    只注入待校验 Cookie → 访问小红书首页判定登录态。

    必须清空的原因：9223 是常驻浏览器，context 里可能残留此前注入的有效 Cookie；不清空直接
    注入校验，explore 会带着旧登录态判定「已登录」，导致校验失效（实测缺陷：登出后仍误报有效）。
    校验完成后恢复：清空 → 重新注入本地已保存的 cookies.json（若存在），维持生产登录态。
    """
    browser = await _connect_profile()
    ctx = browser.contexts[0]
    page = await ctx.new_page()  # 必须新开 tab：绝不占用/关闭用户现有 tab（含 APP 前端页）
    cdp = await ctx.new_cdp_session(page)
    try:
        # ① 清空独立浏览器全部 Cookie（隔离旧登录态）
        await cdp.send("Network.clearBrowserCookies")
        # ② 只注入待校验 Cookie
        try:
            await ctx.add_cookies(cookies)
        except Exception:  # CDP context 的 add_cookies 可能受限，退化为 Network.setCookies
            await cdp.send("Network.setCookies", {"cookies": cookies})
        # ③ 打开小红书首页判定登录态
        try:
            await page.goto(_XHS + "/explore", wait_until="domcontentloaded", timeout=40000)
            await page.evaluate(_EXTRACT_SRC)
            await asyncio.sleep(5)
            result = await page.evaluate(
                "() => ({"
                " logged_in: !!xhsIsLoggedIn(),"
                " url: location.href,"
                " hasLoginUi: /website-login|captcha/i.test(location.href),"
                " noteCards: xhsExtractSearchNotes().length"
                "})"
            )
        finally:
            await _close_page_safe(page)
        return result
    finally:
        # ④ 恢复生产登录态：清空 → 重新注入本地已保存的 cookies.json（若存在）
        try:
            await cdp.send("Network.clearBrowserCookies")
            ck: list = []
            try:
                p = _ROOT / "data" / "cookies.json"
                if p.exists():
                    data = json.loads(p.read_text(encoding="utf8"))
                    ck = data.get("cookies", []) or []
            except Exception:  # noqa: BLE001
                ck = []
            if ck:
                await ctx.add_cookies(ck)
        except Exception:  # noqa: BLE001 恢复失败不阻断校验结果
            pass


async def stop_profile():
    """用后即停：断开连接并杀掉独立 profile Chrome（9223），消除 Dock 多余图标。

    适用场景：一次性动作（Cookie 校验/读取）完成后调用，让生产浏览器按需拉起而非常驻；
    采集/搜索等连续动作期间保持运行（不调用本函数）。
    """
    global _browser
    try:
        if _browser is not None:
            try:
                await _browser.disconnect()
            except Exception:  # noqa: BLE001
                pass
            _browser = None
    except Exception:  # noqa: BLE001
        pass
    try:
        subprocess.run(["pkill", "-9", "-f", "remote-debugging-port=9223"],
                       capture_output=True, timeout=10)
    except Exception:  # noqa: BLE001
        pass
    return True


async def _wait_note_cards(page, timeout_s: float = 45) -> bool:
    """等待搜索页出现笔记卡片（section.note-item）。SPA 渲染慢，返回是否出现。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            n = await page.evaluate("() => document.querySelectorAll('section.note-item').length")
        except Exception:  # noqa: BLE001
            n = 0
        if n > 0:
            return True
        await asyncio.sleep(2.5)
    return False


async def _apply_filters(page, filters: dict):
    """搜索页应用筛选（对应小红书「筛选」弹层）：排序 / 笔记类型 / 发布时间 / 搜索范围 / 位置距离。

    跳过默认值（综合 / 全部 / 不限）：只在用户明确选择非默认条件时点击对应选项。
    点击条件后页面会刷新（URL 带 type 等参数），每项点击后等待结果重新加载。
    """
    wanted = [
        (label, val)
        for label, val in [
            ("排序依据", filters.get("sort")),
            ("笔记类型", filters.get("note_type")),
            ("发布时间", filters.get("time_range")),
            ("搜索范围", filters.get("scope")),
            ("位置距离", filters.get("distance")),
        ]
        if val and val not in ("综合", "全部", "不限", "")
    ]
    if not wanted:
        return
    for _label, val in wanted:
        # 展开筛选弹层（每次点击前确保弹层打开）
        await page.evaluate("""() => {
            const el = [...document.querySelectorAll('span, div')].find(x => (x.innerText || '').trim() === '筛选');
            if (el) el.click();
        }""")
        await asyncio.sleep(1.2)
        await page.evaluate(
            f"""() => {{
                const els = [...document.querySelectorAll('div.tags, div.channel')];
                const t = els.find(x => (x.innerText || '').trim() === {json.dumps(val, ensure_ascii=False)});
                if (t) {{ t.click(); return true; }}
                return false;
            }}"""
        )
        await asyncio.sleep(2.5)  # 点击后页面刷新，等结果重新渲染
    # 收起弹层（避免遮挡滚动提取）
    await page.evaluate("""() => {
        const el = [...document.querySelectorAll('span, div')].find(x => (x.innerText || '').trim() === '收起');
        if (el) el.click();
    }""")
    await asyncio.sleep(1.2)


async def _prepare_search_page(keyword: str, filters: dict | None = None):
    """打开搜索页并等卡片渲染；新版 AI 搜索页（无 note-item）回退旧版 URL。返回可用 page。"""
    url = _XHS + "/search_result?keyword=" + _urlencode(keyword) + "&source=web_search_result_notes"
    page = await _load(url, 2500)
    appeared = await _wait_note_cards(page, timeout_s=45)
    if not appeared:
        # AI 搜索页（无 section.note-item）→ 回退无 source 的旧版搜索
        await _close_page_safe(page)
        page = await _load(_XHS + "/search_result?keyword=" + _urlencode(keyword), 2500)
        appeared = await _wait_note_cards(page, timeout_s=45)
    if appeared:
        # 应用筛选条件后再采集（页面可能刷新，需再次等待卡片）
        await _apply_filters(page, filters or {})
        if filters:
            await _wait_note_cards(page, timeout_s=30)
    return page


async def _drain_page(page, items: list, seen: set) -> int:
    """提取当前页可见卡片并入 items（广告卡过滤 + 去重）。返回新增条数。

    提取前必须注入 _EXTRACT_SRC：SPA 页面刷新/筛选点击会丢失 window 全局函数，
    每次提取前重新注入（幂等，开销可忽略）。
    """
    await page.evaluate(_EXTRACT_SRC)  # 注入提取脚本（xhsExtractSearchNotes 等）
    got = await page.evaluate("() => xhsExtractSearchNotes()")
    added = 0
    for it in got:
        k = it.get("feed_id") or it.get("id")
        # 过滤推广/广告卡片：真实笔记 id 是 24 位 hex（推广位是 UUID 等其他格式）
        if not re.fullmatch(r"[0-9a-f]{24}", str(k or "")):
            continue
        if k not in seen:
            seen.add(k)
            items.append(it)
            added += 1
    return added


def _should_stop_collect(count: int, limit: int, stale: int, added: int, should_stop) -> tuple[bool, int]:
    """滚动采集终止判定：采满 / 用户停止 / 页面不再增长（stale≥2 且有结果）。返回 (是否停止, 新 stale)。"""
    if count >= limit:
        return True, stale
    if should_stop and should_stop():
        return True, stale
    if added == 0:
        stale += 1
        if stale >= 2 and count > 0:
            return True, stale
    else:
        stale = 0
    return False, stale


async def search(keyword: str, limit: int = 30, on_progress=None, should_stop=None, filters: dict | None = None) -> dict:
    """任务式采集：滚动平台搜索页逐批提取，支持去重合并、进度回调与中途停止。

    - on_progress(n)：每批提取后回调已采集数量（供任务进度展示）
    - should_stop()：返回 True 时提前收尾（用户点「停止采集」）
    - filters：搜索筛选（排序/笔记类型/发布时间/搜索范围/位置距离），打开页后应用
    - 总时长上限 120s：平台 AI 搜索页渲染慢时的兜底，超时返回已采集部分
    """
    page = await _prepare_search_page(keyword, filters)
    items: list = []
    seen: set = set()
    stale = 0
    deadline = time.time() + 120
    try:
        while time.time() < deadline:
            added = await _drain_page(page, items, seen)
            if on_progress:
                on_progress(len(items))
            done, stale = _should_stop_collect(len(items), limit, stale, added, should_stop)
            if done:
                break
            await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1.8)
        stopped = bool(should_stop and should_stop())
        return {"keyword": keyword, "count": len(items), "items": items, "stopped": stopped}
    finally:
        await _close_page_safe(page)


async def note(note_id: str, xsec_token: str = "", wait_ms: int = 5000) -> dict:
    url = (
        f"{_XHS}/search_result/{note_id}?xsec_token={_urlencode(xsec_token)}&xsec_source=pc_search"
        if xsec_token else f"{_XHS}/explore/{note_id}"
    )
    page = await _load(url, wait_ms)
    try:
        return await page.evaluate("() => xhsExtractNoteDetail()")
    finally:
        await _close_page_safe(page)


async def user(uid: str, wait_ms: int = 5000) -> dict:
    page = await _load(f"{_XHS}/user/profile/{uid}", wait_ms)
    try:
        return await page.evaluate("() => xhsExtractUser()")
    finally:
        await _close_page_safe(page)


async def clients(note_id: str, xsec_token: str = "") -> dict:
    """采集笔记评论区互动用户名单（P4）：打开原作品页 → 等待评论区渲染 → DOM 提取。"""
    url = (
        f"{_XHS}/search_result/{note_id}?xsec_token={_urlencode(xsec_token)}&xsec_source=pc_search"
        if xsec_token else f"{_XHS}/explore/{note_id}"
    )
    page = await _load(url, 6000)
    try:
        # 评论区懒加载：滚动到精确容器 .interaction-container（旧 [class*=comment] 已失效）触发渲染
        for _ in range(3):
            await page.evaluate("""() => {
              const c = document.querySelector('.interaction-container') || [...document.querySelectorAll('[class*="comment"]')].pop();
              if (c) c.scrollIntoView({ block: "center" });
            }""")
            await asyncio.sleep(1.2)
        # 评论区默认折叠（只显示几条）：尝试点击「展开/查看全部评论」类按钮；无按钮则滚动到底加载
        for _ in range(2):
            clicked = await page.evaluate("""() => {
              const btns = [...document.querySelectorAll('button, [role="button"], div, span')].filter(e => {
                const t = (e.innerText || '').trim();
                return t.length < 20 && /(展开|查看)?全部评论|查看更多评论/.test(t);
              });
              for (const b of btns) {
                if (b.tagName === 'BUTTON' || b.getAttribute('role') === 'button' || b.childElementCount === 0) {
                  try { b.click(); return true; } catch (e) { return false; }
                }
              }
              return false;
            }""")
            if clicked:
                await asyncio.sleep(2.5)
                await page.evaluate("""() => {
                  const c = document.querySelector('.interaction-container') || [...document.querySelectorAll('[class*="comment"]')].pop();
                  if (c) c.scrollIntoView({ block: "center" });
                }""")
                await asyncio.sleep(2)
            else:
                await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2)
        return await page.evaluate("() => xhsExtractComments()")
    finally:
        await _close_page_safe(page)


async def open_front() -> dict:
    """打开小红书首页前台标签（引导扫码）；复用已有 explore 标签避免堆积。"""
    page = await _page()
    try:
        await page.goto(_XHS + "/explore", wait_until="domcontentloaded", timeout=40000)
        await page.bring_to_front()
        return {"opened": True, "url": page.url[:60]}
    finally:
        pass  # 前台标签保留给用户扫码


async def stop():
    global _browser, _pw
    if _connect_lock:
        async with _connect_lock:
            try:
                if _browser is not None and _browser.is_connected():
                    await _browser.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                if _pw is not None:
                    await _pw.stop()
            except Exception:  # noqa: BLE001
                pass
            _browser = None
            _pw = None


def _urlencode(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote(s)
