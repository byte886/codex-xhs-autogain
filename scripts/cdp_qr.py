#!/usr/bin/env python3
# 容器内 CDP 截图：连 rod 浏览器最新实例 WS → 小红书页面（含登录弹窗）→ Page.captureScreenshot → /tmp/qr.png
# 由宿主机 `docker exec xhs-mcp python3 /app/data/cdp_qr.py` 调用（后端 /api/qrcode 链路）
import json, socket, base64, os, struct, subprocess, urllib.request, time

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

def grab(port):
    targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5).read())
    pages = [t for t in targets if t["type"] == "page"]
    if not pages:
        raise Exception("no page target")
    pick = next((t for t in pages if "xiaohongshu" in t["url"]), pages[0])
    s = ws_connect(pick["webSocketDebuggerUrl"])
    r = cdp_call(s, 1, "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
    data = base64.b64decode(r["data"])
    open("/tmp/qr.png", "wb").write(data)
    print(f"OK {len(data)} bytes | {pick['url'][:80]}")

last_err = None
for attempt in range(3):
    try:
        port = newest_port()
        grab(port)
        raise SystemExit(0)
    except Exception as e:
        last_err = e
        time.sleep(2)
print("FAIL:", last_err)
raise SystemExit(1)
