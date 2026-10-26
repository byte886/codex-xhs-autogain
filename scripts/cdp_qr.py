#!/usr/bin/env python3
# 容器内 CDP 截图：连 rod 浏览器最新实例 WS → 小红书页面（含登录弹窗）→ Page.captureScreenshot → /tmp/qr.png
# 由宿主机 `docker exec xhs-mcp python3 /app/data/cdp_qr.py` 调用（后端 /api/qrcode 链路）
import json, socket, base64, os, struct, subprocess, urllib.request, time
# 每次执行先清空上次产物，避免读到残留
for _f in ('/tmp/qr_src.txt', '/tmp/qr_b64.txt', '/tmp/qr_state.txt'):
    try: os.remove(_f)
    except FileNotFoundError: pass


def ws_connect(ws_url, timeout=15):
    rest = ws_url.split('://')[1]
    hostpart, path = rest.split('/', 1)
    host = hostpart.split(':')[0]
    port = int(hostpart.split(':')[1]) if ':' in hostpart else 80
    s = socket.create_connection((host, port), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    req = (f"GET /{path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
    s.sendall(req.encode())
    resp = s.recv(4096)
    if b"101" not in resp.split(b"\r\n")[0]:
        raise Exception("handshake fail: " + resp[:100].decode(errors='replace'))
    return s

def ws_send(s, payload):
    data = payload.encode()
    mask = os.urandom(4)
    ln = len(data)
    if ln < 126: h = bytes([0x81, 0x80 | ln])
    elif ln < 65536: h = bytes([0x81, 0x80 | 126]) + struct.pack(">H", ln)
    else: h = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", ln)
    s.sendall(h + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

def ws_recv(s):
    s.settimeout(40)
    def readn(n):
        buf = b""
        while len(buf) < n:
            c = s.recv(n - len(buf))
            if not c: raise Exception("closed")
            buf += c
        return buf
    h = readn(2)
    ln = h[1] & 0x7F
    if ln == 126: ln = struct.unpack(">H", readn(2))[0]
    elif ln == 127: ln = struct.unpack(">Q", readn(8))[0]
    mask = readn(4) if h[1] & 0x80 else None
    data = readn(ln)
    if mask: data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return data

def cdp_call(s, mid, method, params=None):
    msg = {"id": mid, "method": method}
    if params: msg["params"] = params
    ws_send(s, json.dumps(msg))
    while True:
        m = json.loads(ws_recv(s).decode())
        if m.get("id") == mid:
            if "error" in m: raise Exception(str(m["error"]))
            return m.get("result", {})

def newest_port():
    fp = subprocess.run(["sh", "-c", "ls -t /tmp/rod/user-data/*/DevToolsActivePort 2>/dev/null | head -1"],
                        capture_output=True, text=True).stdout.strip()
    if not fp:
        raise Exception("no DevToolsActivePort")
    return int(open(fp).readline().strip())

def grab(port, check_only=False, click_login=False, fp_probe=False, create_qr=False):
    targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5).read())
    pages = [t for t in targets if t["type"] == "page"]
    if not pages:
        raise Exception("no page target")
    pick = next((t for t in pages if "xiaohongshu" in t["url"]), pages[0])
    s = ws_connect(pick["webSocketDebuggerUrl"])
    # 1) 提取登录弹窗二维码 img 的 src（URL 或 dataURI）+ 页面/弹窗状态
    js = r'''(() => {
      const bodyText = document.body ? document.body.innerText.slice(0, 600) : '';
      const imgs = [...document.images];
      const qr = imgs.find(i =>
        i.naturalWidth >= 100 && i.naturalWidth <= 500 &&
        i.naturalHeight >= 100 && i.naturalHeight <= 500 &&
        Math.abs(i.naturalWidth - i.naturalHeight) < 30
      );
      const state =
        bodyText.includes('请勿频繁操作') ? 'rate_limited' :
        bodyText.includes('登录超限') || bodyText.includes('设备登录超限') ? 'device_limit_qr' :
        bodyText.includes('扫码登录') ? 'login_modal' :
        bodyText.includes('登录') ? 'login_page' : 'no_login';
      if (qr) return { found: true, src: qr.src, w: qr.naturalWidth, h: qr.naturalHeight, state };
      const big = imgs.filter(i => i.naturalWidth >= 100 && i.naturalWidth <= 600);
      return { found: false, state, big: big.slice(0, 5).map(i => ({ src: i.src.slice(0, 160), w: i.naturalWidth, h: i.naturalHeight })) };
    })()'''
    try:
        r = cdp_call(s, 1, "Runtime.evaluate", {"expression": js, "returnByValue": True})
        qrinfo = r.get("result", {}).get("value", {})
        open("/tmp/qr_state.txt", "w").write(qrinfo.get("state", "unknown"))
        if qrinfo.get("found"):
            src = qrinfo["src"]
            if src.startswith("data:image"):
                b64 = src.split(",", 1)[1]
                open("/tmp/qr_b64.txt", "w").write(b64)
                print(f"QR_DATA_URI {len(b64)//1024}KB w={qrinfo['w']} h={qrinfo['h']} state={qrinfo.get('state')}")
            else:
                open("/tmp/qr_src.txt", "w").write(src)
                print(f"QR_URL {src[:160]} w={qrinfo['w']} h={qrinfo['h']} state={qrinfo.get('state')}")
        else:
            print(f"QR_NOT_FOUND state={qrinfo.get('state')} {json.dumps(qrinfo.get('big', []))[:160]}")
    except Exception as e:
        print("QR_EVAL_FAIL", str(e)[:120])
    if create_qr:
        js4 = r'''fetch('https://edith.xiaohongshu.com/api/sns/web/v1/login/qrcode/create', {
          method: 'POST',
          headers: {'Content-Type': 'application/json;charset=UTF-8'},
          body: JSON.stringify({qr_type: 1}),
          credentials: 'include'
        }).then(r => r.json()).then(d => JSON.stringify(d)).catch(e => 'ERR:' + e.message)'''
        r = cdp_call(s, 5, "Runtime.evaluate", {"expression": js4, "awaitPromise": True, "returnByValue": True})
        print("CREATE_QR", json.dumps(r.get("result", {}).get("value", ""))[:800])
    if fp_probe:
        js3 = r'''(() => ({
          ua: navigator.userAgent.slice(0, 120),
          webdriver: navigator.webdriver,
          platform: navigator.platform,
          languages: navigator.languages.join(','),
          cores: navigator.hardwareConcurrency,
          memory: navigator.deviceMemory,
          chromeObj: typeof window.chrome !== 'undefined',
          plugins: navigator.plugins ? navigator.plugins.length : -1,
          webgl: (() => { try { const c = document.createElement('canvas'); const g = c.getContext('webgl'); return g ? g.getParameter(g.getParameter(0x9245)) : 'no'; } catch(e) { return 'err'; } })(),
          maxTouch: navigator.maxTouchPoints,
          vendor: navigator.vendor
        }))()'''
        r = cdp_call(s, 4, "Runtime.evaluate", {"expression": js3, "returnByValue": True})
        print("CONTAINER_FP", json.dumps(r.get("result", {}).get("value", {})))
    if click_login:
        js2 = r'''(() => {
          const all = [...document.querySelectorAll('button, a, [class*=btn], [class*=login]')];
          const target = all.find(el => (el.innerText || '').trim() === '登录' && el.offsetParent !== null)
                      || all.find(el => (el.innerText || '').includes('登录') && el.offsetWidth > 30 && el.offsetParent !== null);
          if (target) { target.click(); return { clicked: true, tag: target.tagName, cls: (target.className||'').toString().slice(0,60), text: target.innerText.slice(0,20) }; }
          return { clicked: false, candidates: all.slice(0,20).map(el => (el.innerText||'').trim().slice(0,16)).filter(Boolean) };
        })()'''
        r = cdp_call(s, 3, "Runtime.evaluate", {"expression": js2, "returnByValue": True})
        print("CLICK_LOGIN", json.dumps(r.get("result", {}).get("value", {}))[:200])
        import time as _t; _t.sleep(3)
    if check_only:
        return
    # 2) 兜底：整页截图
    r = cdp_call(s, 2, "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
    data = base64.b64decode(r["data"])
    open("/tmp/qr.png", "wb").write(data)
    print(f"OK {len(data)} bytes | {pick['url'][:80]}")

import sys
check_only = "--check" in sys.argv
click_login = "--click-login" in sys.argv
fp_probe = "--fp" in sys.argv
create_qr = "--create-qr" in sys.argv
last_err = None
for attempt in range(3):
    try:
        port = newest_port()
        grab(port, check_only=check_only, click_login=click_login, fp_probe=fp_probe, create_qr=create_qr)
        raise SystemExit(0)
    except Exception as e:
        last_err = e
        time.sleep(2)
print("FAIL:", last_err)
raise SystemExit(1)
