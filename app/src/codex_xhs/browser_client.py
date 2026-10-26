"""Playwright 浏览器执行器 —— 后端浏览器控制统一为 Python 单栈（替代 Node daemon/actions/connect）。

设计：
- 同进程常驻：uvicorn 内第一次调用时连接 Chrome 一次，此后 search/note/cookie/login 全部复用
  同一连接（不再每次操作都新起进程/每次连接弹授权窗）。
- 连接方式可插拔（生产不依赖豆包/调试通道）：
    XHS_BROWSER_MODE=daily    默认：连日常 Chrome（复用已扫码登录态；首次连接弹一次授权窗，自动代点）
    XHS_BROWSER_MODE=profile  交付：APP 自拉独立 profile 真实 Chrome（无授权弹窗；需客户扫码一次）
- DOM 提取复用 extension/lib/extract.js（页面内 evaluate 注入，单一提取源）。

依赖：pip install playwright（连接已有 Chrome 不需要下载 chromium）。
"""
import os
import pathlib
import subprocess
import threading
import time

from playwright.sync_api import sync_playwright

_ROOT = pathlib.Path(__file__).resolve().parents[3]  # app/src/codex_xhs -> 项目根
_EXTRACT_SRC = (_ROOT / "extension" / "lib" / "extract.js").read_text(encoding="utf8")
_XHS = "https://www.xiaohongshu.com"

_pw = None
_browser = None
_lock = threading.Lock()
_press_lock = threading.Lock()  # 授权代点进程内串行（uvicorn 单进程，无需文件锁）
_fail_until = 0.0
_PRESS_SCRIPT = _ROOT / "scripts" / "browser" / "cdp" / "press_allow.applescript"


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


def _press_consent_once(restore_to: str = "") -> bool:
    """点一次授权弹窗。进程内线程锁串行（不用文件锁：文件锁在 osascript 偶发慢时自锁，
    导致后续代点全部等锁放弃、弹窗永远点不掉——已实测复现）。"""
    with _press_lock:
        try:
            r = subprocess.run(
                ["osascript", str(_PRESS_SCRIPT), restore_to],
                capture_output=True, timeout=5,
            )
            return b"pressed=true" in r.stdout
        except Exception:  # noqa: BLE001
            return False


def _connect_daily_with_consent() -> object:
    """连接日常 Chrome；连接握手期间并发代点授权弹窗（Chrome 144+ 每次连接必弹，官方无法关闭）。"""
    global _pw
    endpoint = _ws_endpoint()
    stop = threading.Event()

    def presser():
        while not stop.is_set():
            _press_consent_once()
            time.sleep(0.6)

    t = threading.Thread(target=presser, daemon=True)
    t.start()
    try:
        _pw = sync_playwright().start()
        return _pw.chromium.connect_over_cdp(endpoint, timeout=20000)
    finally:
        stop.set()


def get_browser() -> object:
    """常驻连接（线程安全）。连接失败冷却 20s 后自动重试。"""
    global _browser, _fail_until
    with _lock:
        if _browser is not None and _browser.is_connected():
            return _browser
        now = time.monotonic()
        if _fail_until > now:
            raise RuntimeError(f"浏览器连接冷却中（{int(_fail_until - now)}s 后再试）")
        try:
            if os.environ.get("XHS_BROWSER_MODE") == "profile":
                _browser = _connect_profile()
            else:
                _browser = _connect_daily_with_consent()
            return _browser
        except Exception as e:  # noqa: BLE001
            _fail_until = time.monotonic() + 20
            raise RuntimeError(f"连接浏览器失败: {e}") from e


def _connect_profile() -> object:
    """交付模式：APP 自拉独立 profile Chrome（无授权弹窗），连接失败时自动拉起。"""
    global _pw
    port = int(os.environ.get("XHS_PROFILE_PORT", "9223"))
    profile_dir = os.environ.get("XHS_PROFILE_DIR") or os.path.join(
        os.path.expanduser("~"), ".codex-xhs-profile"
    )
    if _pw is None:
        _pw = sync_playwright().start()
    try:
        return _pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=8000)
    except Exception:
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        os.makedirs(profile_dir, exist_ok=True)
        subprocess.Popen(
            [chrome, f"--user-data-dir={profile_dir}", f"--remote-debugging-port={port}",
             "--no-first-run", "--no-default-browser-check", "--new-window"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(30):
            time.sleep(1)
            try:
                return _pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=5000)
            except Exception:  # noqa: BLE001
                continue
        raise RuntimeError("独立 Chrome 调试端口未就绪")


def _page() -> object:
    """取一个可用的页面 target（后台新建，不抢用户前台焦点；失败退化为前台新页）。"""
    ctx = get_browser().contexts[0]
    try:
        src = ctx.pages[0]
        cdp = ctx.new_cdp_session(src)
        resp = cdp.send("Target.createTarget", {"url": "about:blank", "background": True})
        tid = resp["targetId"]
        for _ in range(50):
            for p in ctx.pages:
                if p.url == "about:blank" and len(ctx.pages) > 1 and p != src:
                    # Playwright Python 无公开 targetId；用「非首标签且 about:blank」近似匹配
                    return p
            time.sleep(0.2)
    except Exception:  # noqa: BLE001
        pass
    return ctx.new_page()


def _load(url: str, wait_ms: int = 5000):
    page = _page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=40000)
        page.evaluate(_EXTRACT_SRC)
        time.sleep(wait_ms / 1000)
        return page
    except Exception:
        try:
            page.close()
        except Exception:  # noqa: BLE001
            pass
        raise


def login_check() -> dict:
    page = _load(_XHS + "/explore", 6000)
    try:
        return page.evaluate(
            "() => ({"
            " logged_in: !!xhsIsLoggedIn(),"
            " url: location.href,"
            " hasLoginUi: /website-login|captcha/i.test(location.href),"
            " noteCards: xhsExtractSearchNotes().length"
            "})"
        )
    finally:
        page.close()


def cookies() -> dict:
    page = _load(_XHS + "/explore", 3000)
    try:
        cdp = page.context.new_cdp_session(page)
        data = cdp.send("Network.getAllCookies")
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
        page.close()


def search(keyword: str, limit: int = 30) -> dict:
    url = _XHS + "/search_result?keyword=" + _urlencode(keyword) + "&source=web_search_result_notes"
    page = _load(url, 2500)
    try:
        items = []
        for _ in range(12):
            items = page.evaluate("() => xhsExtractSearchNotes()")
            if len(items) >= limit:
                break
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2.5)
        return {"keyword": keyword, "count": len(items[:limit]), "items": items[:limit]}
    finally:
        page.close()


def note(note_id: str, xsec_token: str = "") -> dict:
    url = (
        f"{_XHS}/search_result/{note_id}?xsec_token={_urlencode(xsec_token)}&xsec_source=pc_search"
        if xsec_token else f"{_XHS}/explore/{note_id}"
    )
    page = _load(url, 5000)
    try:
        return page.evaluate("() => xhsExtractNoteDetail()")
    finally:
        page.close()


def user(uid: str) -> dict:
    page = _load(f"{_XHS}/user/profile/{uid}", 5000)
    try:
        return page.evaluate("() => xhsExtractUser()")
    finally:
        page.close()


def open_front() -> dict:
    """打开小红书首页前台标签（引导扫码）；复用已有 explore 标签避免堆积。"""
    page = _page()
    try:
        page.goto(_XHS + "/explore", wait_until="domcontentloaded", timeout=40000)
        page.bring_to_front()
        return {"opened": True, "url": page.url[:60]}
    finally:
        pass  # 前台标签保留给用户扫码


def _urlencode(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote(s)


def stop():
    global _browser, _pw
    with _lock:
        try:
            if _browser is not None and _browser.is_connected():
                _browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if _pw is not None:
                _pw.stop()
        except Exception:  # noqa: BLE001
            pass
        _browser = None
        _pw = None
