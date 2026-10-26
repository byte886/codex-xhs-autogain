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
import os
import pathlib
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
    port_file = os.environ.get(
        "CHROME_DEVTOOLS_ACTIVE_PORT",
        os.path.expanduser("~/Library/Application Support/Google/Chrome/DevToolsActivePort"),
    )
    if not os.path.exists(port_file):
        raise RuntimeError(f"找不到 DevToolsActivePort：{port_file}（请确认日常 Chrome 已打开且开启远程调试）")
    lines = open(port_file, encoding="utf8").read().strip().split("\n")
    if len(lines) < 2 or not lines[0].strip().isdigit():
        raise RuntimeError(f"DevToolsActivePort 内容异常：{lines}")
    return f"ws://127.0.0.1:{lines[0].strip()}{lines[1].strip()}"


async def _press_consent_once(restore_to: str = "") -> bool:
    """点一次授权弹窗（子进程调 AppleScript）。async 侧用 asyncio.Lock 串行，
    避免并发 osascript 拥塞 System Events（文件锁版本已实测自锁弃用）。"""
    async with _press_lock:
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
            else:
                _browser = await _connect_daily_with_consent()
            return _browser
        except Exception as e:  # noqa: BLE001
            _fail_until = time.monotonic() + 20
            raise RuntimeError(f"连接浏览器失败: {e}") from e


async def _connect_profile():
    """交付模式：APP 自拉独立 profile Chrome（无授权弹窗），连接失败时自动拉起。"""
    global _pw
    port = int(os.environ.get("XHS_PROFILE_PORT", "9223"))
    profile_dir = os.environ.get("XHS_PROFILE_DIR") or os.path.join(
        os.path.expanduser("~"), ".codex-xhs-profile"
    )
    if _pw is None:
        _pw = await async_playwright().start()
    try:
        return await _pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=8000)
    except Exception:
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        os.makedirs(profile_dir, exist_ok=True)
        subprocess.Popen(
            [chrome, f"--user-data-dir={profile_dir}", f"--remote-debugging-port={port}",
             "--no-first-run", "--no-default-browser-check", "--new-window"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(30):
            await asyncio.sleep(1)
            try:
                return await _pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=5000)
            except Exception:  # noqa: BLE001
                continue
        raise RuntimeError("独立 Chrome 调试端口未就绪")


async def _page():
    """取一个可用的页面 target（后台新建，不抢用户前台焦点；失败退化为前台新页）。"""
    ctx = (await get_browser()).contexts[0]
    try:
        src = ctx.pages[0]
        cdp = await ctx.new_cdp_session(src)
        resp = await cdp.send("Target.createTarget", {"url": "about:blank", "background": True})
        for _ in range(50):
            for p in ctx.pages:
                if p.url == "about:blank" and len(ctx.pages) > 1 and p != src:
                    return p
            await asyncio.sleep(0.2)
    except Exception:  # noqa: BLE001
        pass
    return await ctx.new_page()


async def _load(url: str, wait_ms: int = 5000):
    page = await _page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=40000)
        await page.evaluate(_EXTRACT_SRC)
        await asyncio.sleep(wait_ms / 1000)
        return page
    except Exception:
        try:
            await page.close()
        except Exception:  # noqa: BLE001
            pass
        raise


async def login_check() -> dict:
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
        await page.close()


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
        await page.close()


async def verify_cookies(cookies: list[dict]) -> dict:
    """校验导入的 Cookie（生产路径预演）：注入独立 profile Chrome → 访问小红书首页判定登录态。

    独立 profile（XHS_PROFILE_PORT=9223）是 APP 交付形态的专用浏览器，可随意注入 Cookie，
    不污染用户日常 Chrome。校验通过即说明生产环境导入链路可用。"""
    browser = await _connect_profile()
    ctx = browser.contexts[0]
    try:
        try:
            await ctx.add_cookies(cookies)
        except Exception:  # CDP context 的 add_cookies 可能受限，退化为 Network.setCookies
            cdp = await ctx.new_cdp_session(ctx.pages[0] if ctx.pages else await ctx.new_page())
            await cdp.send("Network.setCookies", {"cookies": cookies})
        page = await ctx.new_page()
        try:
            await page.goto(_XHS + "/explore", wait_until="domcontentloaded", timeout=40000)
            await page.evaluate(_EXTRACT_SRC)
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
            await page.close()
    finally:
        # 校验用 profile Chrome 保持运行（后续采集/登录可切换复用）；不关闭浏览器
        pass


async def search(keyword: str, limit: int = 30) -> dict:
    url = _XHS + "/search_result?keyword=" + _urlencode(keyword) + "&source=web_search_result_notes"
    page = await _load(url, 2500)
    try:
        items = []
        for _ in range(12):
            items = await page.evaluate("() => xhsExtractSearchNotes()")
            if len(items) >= limit:
                break
            await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2.5)
        return {"keyword": keyword, "count": len(items[:limit]), "items": items[:limit]}
    finally:
        await page.close()


async def note(note_id: str, xsec_token: str = "") -> dict:
    url = (
        f"{_XHS}/search_result/{note_id}?xsec_token={_urlencode(xsec_token)}&xsec_source=pc_search"
        if xsec_token else f"{_XHS}/explore/{note_id}"
    )
    page = await _load(url, 5000)
    try:
        return await page.evaluate("() => xhsExtractNoteDetail()")
    finally:
        await page.close()


async def user(uid: str) -> dict:
    page = await _load(f"{_XHS}/user/profile/{uid}", 5000)
    try:
        return await page.evaluate("() => xhsExtractUser()")
    finally:
        await page.close()


async def clients(note_id: str, xsec_token: str = "") -> dict:
    """采集笔记评论区互动用户名单（P4）：打开原作品页 → 等待评论区渲染 → DOM 提取。"""
    url = (
        f"{_XHS}/search_result/{note_id}?xsec_token={_urlencode(xsec_token)}&xsec_source=pc_search"
        if xsec_token else f"{_XHS}/explore/{note_id}"
    )
    page = await _load(url, 6000)
    try:
        # 评论区懒加载：滚动到评论区触发渲染 + 等待
        for _ in range(3):
            await page.evaluate("""() => {
              const cs = document.querySelectorAll('[class*="comment"]');
              if (cs.length) cs[cs.length - 1].scrollIntoView({ block: "center" });
            }""")
            await asyncio.sleep(1.5)
        return await page.evaluate("() => xhsExtractComments()")
    finally:
        await page.close()


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
