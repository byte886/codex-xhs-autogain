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
        # 窗口策略：生产默认 --start-minimized（后台隐形，Dock 一个图标）；
        # XHS_PROFILE_WINDOW=normal 时正常显示（演示/用户扫码需要看到窗口）。
        win_args = [] if os.environ.get("XHS_PROFILE_WINDOW") == "normal" else ["--start-minimized"]
        subprocess.Popen(
            [chrome, f"--user-data-dir={profile_dir}", f"--remote-debugging-port={port}",
             "--no-first-run", "--no-default-browser-check", "--new-window", *win_args],
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


async def cleanup_tabs():
    """清理 9223 生产 Chrome 中与任务无关的标签页：保留第 1 个，其余全部关闭，保留页归位 about:blank。

    使用场景：测试/演示前后、以及长时间运行后定期执行，避免反复 _page() 建后台页造成 tab 堆积。
    对生产会话安全：只关多余页面、不动浏览器本身；连接复用 get_browser() 现有连接。
    """
    try:
        browser = await get_browser()
        ctx = browser.contexts[0]
        pages = ctx.pages
        if len(pages) <= 1:
            return 0
        keep = pages[0]
        closed = 0
        for p in pages[1:]:
            try:
                await p.close()
                closed += 1
            except Exception:  # noqa: BLE001
                continue
        try:
            if keep.url != "about:blank":
                await keep.goto("about:blank")
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
            await page.close()


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
            await page.close()
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
        await page.close()


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
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
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
            await page.close()
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
