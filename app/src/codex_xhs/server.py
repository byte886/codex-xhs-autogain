"""G2 后端壳：把 xiaohongshu-mcp 封装成 REST API + SQLite 读写 + 静态前端服务。

运行:
  uvicorn server:app --host 127.0.0.1 --port 8791

API:
  GET  /                    → 前端界面（app/web/index.html）
  GET  /api/login_status    → 登录状态
  POST /api/search          → 搜索+详情补全+评分+落库（同步返回新增数）
  GET  /api/notes           → 笔记列表（评分排序，支持关键词过滤）
  GET  /api/note/{feed_id}  → 单条详情（含 desc/图片列表）
  POST /api/clone           → 一键克隆：拉详情+下载图片，返回可编辑载荷
  POST /api/publish         → 发布（默认仅自己可见）
  GET  /api/logs            → 发布记录
"""
import datetime
import io
import json
import os
import random
import sqlite3
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---- 路径 ----
BASE = Path(__file__).resolve().parents[3]  # ~/Desktop/codex-zhuke（server.py 在 app/src/codex_xhs/ 下）
DB_PATH = BASE / "data" / "xhs_notes.db"
WEB_DIR = BASE / "app" / "web"
IMAGES_DIR = BASE / "data" / "images"

_INTERVAL = (1.0, 1.8)

from codex_xhs.score import rank_notes  # noqa: E402
from codex_xhs.collect_pipeline import (  # noqa: E402
    get_feed_detail,
    get_user_profile,
    search_via_browser,
)

app = FastAPI(title="Codex 自动获客 · 本地工具")


@app.on_event("startup")
async def _startup_cleanup():
    """启动时清理：残留任务 tab + 无引用本地图片。best-effort 不阻塞启动。"""
    try:
        from codex_xhs import browser_client
        await asyncio.wait_for(browser_client.cleanup_tabs(), timeout=15)
    except Exception:  # noqa: BLE001
        pass
    try:
        _cleanup_orphan_images()
    except Exception:  # noqa: BLE001
        pass


@app.post("/api/cleanup_images")
def cleanup_images():
    """前端启动时触发：清理无引用本地图片（与后端 startup 双保险）。"""
    removed = _cleanup_orphan_images()
    return {"ok": True, "removed": removed}

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
IMAGES_DIR.mkdir(parents=True, exist_ok=True)


def _img_name(feed_id: str, suffix: str = "cover") -> str:
    """本地图片文件名：feed_id 含 #（XHS 原特征），# 在 URL 里是 fragment 会被截断 → 替换为 _。"""
    return f"{feed_id.replace('#', '_')}_{suffix}.jpg"


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    _init_tables(con)
    return con


def _init_tables(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            feed_id TEXT PRIMARY KEY, keyword TEXT, title TEXT, note_type TEXT,
            user_id TEXT, nickname TEXT, avatar TEXT, cover_url TEXT, xsec_token TEXT,
            desc TEXT, ip_location TEXT,
            liked_count INTEGER, collected_count INTEGER, comment_count INTEGER,
            shared_count INTEGER, fans_count INTEGER, publish_time INTEGER,
            score REAL, tag TEXT, fan_ratio REAL, fetched_at TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS publish_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feed_id TEXT, title TEXT, content TEXT, tags TEXT, visibility TEXT,
            status TEXT, note_url TEXT, created_at TEXT
        )
    """)
    # v3 拟人化护栏需要 raw 列（风控熔断关键词检测）；老库补列
    try:
        con.execute("ALTER TABLE publish_logs ADD COLUMN raw TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
    # P4 采集客户：互动用户名单（评论用户）
    con.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            uid TEXT, feed_id TEXT, nickname TEXT, comment TEXT, profile TEXT, created_at TEXT,
            PRIMARY KEY (uid, feed_id)
        )
    """)
    # 存草稿（长期保留资产；区别于搜索/采集结果——用完即失效）
    con.execute("""
        CREATE TABLE IF NOT EXISTS drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feed_id TEXT, title TEXT, content TEXT, tags TEXT,
            visibility TEXT, status TEXT DEFAULT 'draft', created_at TEXT
        )
    """)
    con.commit()


# ================= 登录 =================
# 登录态缓存：Chrome CDP 连接会弹「远程调试授权」窗（每次连接必弹，官方无法关闭）。
# 前端轮询 login_status 若每次都连 Chrome → 每几秒弹一次窗（用户痛点「关了又开」）。
# 因此缓存检测结果 TTL=30s，轮询打缓存，不反复连 Chrome。
_LOGIN_CACHE = {"t": 0.0, "data": None}
_LOGIN_CACHE_TTL = 30.0

def _read_cookie_status(p: Path) -> tuple[bool, str, bool]:
    """读本地 cookies.json 判定登录态：返回 (logged_in, web_session 摘要, expires 是否有效)。

    expires=None 是会话级 Cookie（浏览器会话内一直有效，Chrome 导出不携带过期时间）
    → 视为有效；有 expires 且在未来才有效。"""
    logged_in = False
    ws = ""
    exp_ok = True
    if p.exists():
        data = json.loads(p.read_text(encoding="utf8"))
        cookies = data.get("cookies", [])
        ws = next((c.get("value", "") for c in cookies if c.get("name") == "web_session"), "")
        now_ts = int(time.time())
        exps = [c.get("expires") for c in cookies if c.get("name") == "web_session"]
        exp_ok = (not exps) or any(e is None or (e or 0) >= now_ts for e in exps)
        logged_in = bool(ws) and exp_ok
    return logged_in, ws, exp_ok


@app.get("/api/login_status")
async def login_status():
    """登录态检测（轻量版）：只读本地已保存 cookies.json——存在且含未过期的 web_session 即视为已登录。

    刻意【不拉独立浏览器】：避免登录轮询/页面打开时反复拉起 9223（Dock 图标/窗口干扰）。
    浏览器深度校验（explore 真实判定）仅在导入 Cookie / 读取 Chrome / 采集前置等按需动作中进行。
    """
    import time

    now = time.monotonic()
    if _LOGIN_CACHE["data"] is not None and now - _LOGIN_CACHE["t"] < _LOGIN_CACHE_TTL:
        return _LOGIN_CACHE["data"]

    try:
        logged_in, ws, exp_ok = _read_cookie_status(BASE / "data" / "cookies.json")
        result = {
            "status": "logged_in" if logged_in else "not_logged_in",
            "logged_in": logged_in,
            "nickname": "",
            "raw": f"local-cookie web_session={'有' if ws else '无'} expires={'未来' if exp_ok else '过期'}",
        }
    except Exception as e:
        result = {"status": "unknown", "logged_in": False, "nickname": "", "raw": f"本地判定失败: {str(e)[:80]}"}

    _LOGIN_CACHE["t"] = now
    _LOGIN_CACHE["data"] = result
    return result


@app.get("/api/qrcode")
async def qrcode():
    """Cookie 失效时前端引导扫码：新架构（Docker 已退役）下登录发生在外部 Chrome 内，
    本接口负责在 Chrome 中打开小红书首页让用户直接扫码，并返回引导信息。
    前端配合轮询 /api/login_status 直到登录成功。"""
    import subprocess

    # 在 Chrome 中打开小红书首页（前台标签，用户可直接扫码）——经常驻 Playwright 连接，不额外弹窗
    try:
        from codex_xhs import browser_client
        await browser_client.open_front()
        return {
            "ok": True,
            "guide": "请在打开的 Chrome 小红书首页中扫码登录；登录成功后点「我已扫码完成」检测。",
            "url": "https://www.xiaohongshu.com/explore",
            "expires_in": 300,
        }
    except Exception as e:
        return {"ok": False, "error": f"打开 Chrome 小红书首页失败：{str(e)[:100]}"}



class SearchReq(BaseModel):
    keyword: str
    limit: int = 20


async def _require_login():
    """登录态熔断：搜索/克隆/采集客户前检查登录态（30s 缓存，不反复连 Chrome）。
    仅当明确判定为「未登录」时拦截；检测异常（unknown）放行，避免误伤。"""
    st = await login_status()
    if st.get("status") == "not_logged_in":
        raise HTTPException(
            403,
            "小红书登录态已失效：请导入有效 Cookie（左侧面板「导入 Cookie」）或去 Chrome 扫码登录后重试",
        )


@app.post("/api/search")
async def search(req: SearchReq):
    """搜索（同步版，兼容旧前端）：采集 → 评分 → 落库。返回新增条数与最新 TOP 评分。"""
    await _require_login()
    before = _db().execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    try:
        res = await search_via_browser(req.keyword, req.limit)
        notes = res.get("items", [])
    except Exception as e:
        return {"added": 0, "total": before, "top": [], "note": f"搜索未返回结果（{str(e)[:80]}），请确认 Chrome 已打开并登录小红书后重试"}
    if not notes:
        return {"added": 0, "total": before, "top": [], "note": "搜索未返回结果，请确认 Chrome 已打开并登录小红书后重试"}
    saved, total, top = _persist_notes(req.keyword, notes)
    return {"added": saved, "total": total, "top": top, "source": "关键词搜索（本地 Chrome）"}


# ================= 任务式采集（开始采集 / 进度 / 停止） =================
# 采集与展示分离：采集是后台异步任务（滚动平台搜索页逐批提取，支持中途停止），
# 采满目标数 / 平台不再增长 / 用户停止 之后统一落库并评分排序，前端再展示。
# 这样避免"边采边排"导致的列表跳变（先采集后排序，样本固定排序才稳定）。
import asyncio
import uuid

_COLLECT_TASKS: dict = {}


def _rank_and_insert(con, n: dict, keyword: str, now: str) -> bool:
    """单条笔记评分 + 落库。返回是否成功。"""
    try:
        n["keyword"] = keyword
        n["local_cover"] = ""
        try:
            si = rank_notes([n])[0]["score_info"]
        except Exception:  # noqa: BLE001
            si = {"score": 0.0, "tag": "评分失败", "fan_ratio": 0.0}
        con.execute(
            "INSERT OR REPLACE INTO notes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                n.get("feed_id"), keyword, n.get("title", ""), n.get("type", ""),
                n.get("user_id"), n.get("nickname", ""), n.get("avatar", ""),
                n.get("cover_url", ""), n.get("xsec_token", ""), n.get("desc", ""),
                n.get("ip_location", ""),
                int(n.get("liked_count") or 0), int(n.get("collected_count") or 0),
                int(n.get("comment_count") or 0), int(n.get("shared_count") or 0),
                int(n.get("fans_count") or 0), n.get("publish_time"),
                si["score"], si["tag"], si["fan_ratio"], now,
            ),
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def _cleanup_orphan_images() -> int:
    """清理无引用本地图片：当前 notes 批次（feed_id 前缀）保留，其余残留删除。返回删除数。

    背景：封面图下载链路已废弃（前端封面走 cover_url 网络直连，local_cover 从未写入），
    采集/克隆的旧图会无限堆积——启动时（后端 startup + 前端启动请求）双保险清理。"""
    try:
        con = _db()
        ids = {r[0].replace("#", "_") for r in con.execute("SELECT feed_id FROM notes").fetchall()}
        con.close()
    except Exception:  # noqa: BLE001
        ids = set()
    if not IMAGES_DIR.exists():
        return 0
    removed = 0
    for f in IMAGES_DIR.iterdir():
        if not f.is_file():
            continue
        feed_id = f.name.split("_", 1)[0] if "_" in f.name else f.stem
        if feed_id in ids:
            continue
        try:
            f.unlink()
            removed += 1
        except OSError:  # noqa: BLE001
            pass
    return removed


def _persist_notes(keyword: str, notes: list) -> tuple:
    """搜索/采集结果落库：新结果替换旧批次（用完即失效）+ 本地评分。
    返回 (added, total, top)。"""
    con = _db()
    con.execute("DELETE FROM notes")
    con.commit()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    saved = 0
    for n in notes:
        if _rank_and_insert(con, n, keyword, now):
            saved += 1
    con.commit()

    top = con.execute(
        "SELECT feed_id, title, nickname, liked_count, fans_count, score, tag, cover_url "
        "FROM notes ORDER BY score DESC LIMIT 10"
    ).fetchall()
    total = con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    return saved, total, [dict(r) for r in top]


@app.post("/api/collect_start")
async def collect_start(req: SearchReq):
    """启动采集任务：后台滚动平台搜索页逐批提取，直到满额/平台不增长/用户停止。"""
    await _require_login()
    for tid, st in list(_COLLECT_TASKS.items()):
        if st.get("status") == "running":
            st["stop_event"] = True
    tid = uuid.uuid4().hex[:12]
    state = {
        "status": "running", "keyword": req.keyword, "limit": req.limit,
        "collected": 0, "note": "", "stop_event": False,
    }
    _COLLECT_TASKS[tid] = state

    async def worker():
        try:
            res = await search_via_browser(
                req.keyword, req.limit,
                on_progress=lambda n: state.__setitem__("collected", n),
                should_stop=lambda: state["stop_event"],
            )
            items = res["items"]
            state["collected"] = len(items)
            saved, total, top = _persist_notes(req.keyword, items)
            if res.get("stopped"):
                state["status"] = "stopped"
                state["note"] = f"已停止：采集到 {len(items)} 条并完成排序" if len(items) else "已停止（未采到结果）"
            elif len(items) >= req.limit:
                state["status"] = "done"
                state["note"] = f"采集完成：{len(items)} 条已按评分排序"
            else:
                state["status"] = "done"
                state["note"] = f"采集完成：平台仅加载 {len(items)} 条（不足 {req.limit}），已按评分排序"
        except Exception as e:
            state["status"] = "error"
            state["note"] = f"采集失败：{str(e)[:100]}"

    state["task"] = asyncio.create_task(worker())
    return {"ok": True, "task_id": tid, "keyword": req.keyword, "limit": req.limit}


@app.get("/api/collect_progress")
def collect_progress(task_id: str = ""):
    st = _COLLECT_TASKS.get(task_id)
    if not st:
        return {"ok": False, "note": "任务不存在或已过期"}
    return {
        "ok": True, "task_id": task_id, "status": st["status"],
        "keyword": st["keyword"], "limit": st["limit"],
        "collected": st["collected"], "note": st["note"],
    }


class CollectStopReq(BaseModel):
    task_id: str


@app.post("/api/collect_stop")
async def collect_stop(req: CollectStopReq):
    st = _COLLECT_TASKS.get(req.task_id)
    if st and st.get("status") == "running":
        st["stop_event"] = True
        return {"ok": True, "note": "停止已请求，正在收尾…"}
    return {"ok": False, "note": "任务不存在或已结束"}


# ================= 笔记列表 =================
@app.get("/api/notes")
def notes(keyword: str = "", order: str = "score", tag: str = ""):
    con = _db()
    sql = "SELECT * FROM notes WHERE 1=1"
    params = []
    if keyword:
        sql += " AND (keyword LIKE ? OR title LIKE ? OR nickname LIKE ?)"
        params += [f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"]
    if tag:
        sql += " AND tag = ?"
        params.append(tag)
    if order == "score":
        sql += " ORDER BY score DESC"
    else:
        sql += " ORDER BY fetched_at DESC"
    sql += " LIMIT 200"
    rows = con.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # 本地封面存在则替换为本地代理路径（xhscdn 防盗链，直连会 403）
        cf = IMAGES_DIR / _img_name(d["feed_id"])
        d["local_cover"] = f"/app/images/{_img_name(d['feed_id'])}" if cf.exists() else ""
        out.append(d)
    return {"total": len(out), "notes": out}


# ================= 下载图片包（P3） =================
class DownloadImgsReq(BaseModel):
    feed_ids: list[str]


@app.post("/api/download_images")
def download_images(req: DownloadImgsReq):
    """打包选中笔记的本地封面图为 zip。

    图片已在本地磁盘（data/images），直接读文件打包，无网络请求、无风控风险。
    缺失的 feed_id 在响应头 X-Missing 中返回（前端据此提示）。
    """
    if not req.feed_ids:
        raise HTTPException(400, "没有选中的笔记")
    missing: list[str] = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fid in req.feed_ids:
            p = IMAGES_DIR / _img_name(fid)
            if p.exists():
                zf.write(p, arcname=p.name)
            else:
                missing.append(fid)
    data = buf.getvalue()
    fname = f"xhs_images_{int(time.time() * 1000)}.zip"
    headers = {"Content-Disposition": f"attachment; filename={fname}"}
    resp = StreamingResponse(iter([data]), media_type="application/zip", headers=headers)
    resp.headers["X-Missing"] = json.dumps(missing)
    return resp


# ================= 单条详情 =================
@app.get("/api/note/{feed_id}")
async def note_detail(feed_id: str):
    con = _db()
    r = con.execute("SELECT * FROM notes WHERE feed_id=?", (feed_id,)).fetchone()
    if not r:
        raise HTTPException(404, "笔记不存在")
    n = dict(r)
    # 尝试用 token 拉最新详情（含图片列表）——本地浏览器 DOM 提取
    try:
        detail_note = dict(n)
        detail_note = await get_feed_detail(detail_note)
        imgs = detail_note.get("detail_imgs") or []
        if imgs:
            n["images"] = imgs
        if detail_note.get("desc"):
            n["desc"] = detail_note.get("desc")
        if detail_note.get("title"):
            n["title"] = detail_note.get("title")
    except Exception:
        pass
    return n


# ================= 一键克隆 =================
class CloneReq(BaseModel):
    feed_id: str


@app.post("/api/clone")
async def clone(req: CloneReq):
    """克隆：取详情 → 下载图片到 images → 返回可编辑载荷。"""
    await _require_login()
    con = _db()
    r = con.execute("SELECT * FROM notes WHERE feed_id=?", (req.feed_id,)).fetchone()
    if not r:
        raise HTTPException(404, "笔记不存在")
    n = dict(r)

    images = await _collect_images(n, req.feed_id)
    detail_ok = len(images) > 0
    local_images = _download_images(req.feed_id, images)

    n["images_local"] = local_images
    n["detail_ok"] = detail_ok
    return n


async def _collect_images(n: dict, feed_id: str) -> list:
    """克隆用：取详情图片列表（本地浏览器提取，失败回退封面）。返回图片 URL 列表。"""
    images = []
    try:
        dn = dict(n)
        dn = await get_feed_detail(dn)
        imgs = dn.get("detail_imgs") or []
        if imgs:
            images = imgs
            if dn.get("desc"):
                n["desc"] = dn.get("desc")
            if dn.get("title"):
                n["title"] = dn.get("title")
    except Exception:  # noqa: BLE001
        pass
    if not images and n.get("cover_url"):
        images = [n["cover_url"]]
    return images


def _download_image(feed_id: str, idx: int, url: str) -> str:
    """克隆用：下载单张图到本地。返回可访问路径（失败返回空串）。"""
    fname = _img_name(feed_id, suffix=f"p{idx}")
    dest = IMAGES_DIR / fname
    try:
        if not dest.exists():
            reqh = urllib.request.Request(
                url,
                headers={"Referer": "https://www.xiaohongshu.com/", "User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(reqh, timeout=20) as resp:
                dest.write_bytes(resp.read())
        return f"/app/images/{fname}"
    except Exception:  # noqa: BLE001
        return ""


def _download_images(feed_id: str, images: list) -> list:
    """克隆用：并行下载图片（≤9 张）。返回本地路径列表。"""
    with ThreadPoolExecutor(max_workers=6) as ex:
        return list(ex.map(lambda p: _download_image(feed_id, p[0], p[1]), enumerate(images[:9])))


# ================= 采集客户（P4） =================
class ClientsReq(BaseModel):
    feed_id: str
    xsec_token: str = ""


@app.post("/api/clients")
async def collect_clients(req: ClientsReq):
    """采集笔记评论区互动用户名单（P4）：真实浏览器打开原作品页提取评论用户，入库去重。

    图文与视频笔记均支持（评论区都有互动用户）。每次采集访问一次真实页面，频次受浏览器全局锁约束。
    """
    await _require_login()
    try:
        from codex_xhs import browser_client
        # xsec_token 兜底：调用方未传时从本地库自动补查（explore/{id} 直链会 404，必须带 token）
        token = req.xsec_token
        if not token:
            con = _db()
            row = con.execute("SELECT xsec_token FROM notes WHERE feed_id=?", (req.feed_id,)).fetchone()
            con.close()
            if row and row[0]:
                token = row[0]
        users = await browser_client.clients(req.feed_id, token)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"浏览器采集失败：{e}")
    con = _db()
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    added = 0
    for u in users:
        cur = con.execute(
            "INSERT OR IGNORE INTO clients (uid, feed_id, nickname, comment, profile, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (u.get("uid", ""), req.feed_id, u.get("nickname", ""), u.get("comment", ""), u.get("profile", ""), ts),
        )
        added += cur.rowcount
    con.commit()
    return {"total": len(users), "new": added, "users": users}


# ================= Cookie（显示 / 一键从 Chrome 导入） =================
_COOKIE_ORDER = ["web_session", "last_web_session", "a1", "webId", "gid", "id_token", "acw_tc", "websectiga"]


def _cookie_items(cookies: list) -> tuple[list, str]:
    """把本地 cookie 列表整理成展示项（按 _COOKIE_ORDER 排序）+ 提取 web_session 摘要。"""
    items = []
    for i, nm in enumerate(c.get("name", "") for c in cookies):
        items.append({"name": nm, "value": "", "domain": ".xiaohongshu.com", "ord": i})
    items.sort(key=lambda x: (_COOKIE_ORDER.index(x["name"]) if x["name"] in _COOKIE_ORDER else 99, x["name"]))
    ws = next((c.get("value", "") for c in cookies if c.get("name") == "web_session"), "") or ""
    return items, ws


@app.get("/api/cookie_info")
async def cookie_info():
    """只读展示 Cookie 摘要：读本地 data/cookies.json，不连浏览器、不打开页面。

    原实现每次调 browser_client.cookies() → 内部 _load(explore) 会在 Chrome 打开小红书
    首页 tab 再关闭——APP 每次刷新都触发「小红书 tab 闪开又消失」（用户痛点）。改为纯本地读取。"""
    try:
        p = BASE / "data" / "cookies.json"
        data = {}
        if p.exists():
            data = json.loads(p.read_text(encoding="utf8"))
        cookies = data.get("cookies", []) or []
        items, ws = _cookie_items(cookies)
        return {
            "ok": True,
            "count": len(cookies),
            "saved_at": data.get("saved_at", ""),
            "account": (ws[:8] + "…") if ws else "",
            "cookies": items,
        }
    except Exception as e:
        return {"ok": False, "count": 0, "saved_at": "", "cookies": [], "error": f"本地读取失败: {str(e)[:100]}"}


class ImportCookieReq(BaseModel):
    cookie_text: str = ""


def _parse_cookie_text(text: str) -> list[dict]:
    """解析导入的 Cookie 文本：支持 `k=v; k2=v2` 与每行一条 name=value。
    返回 Playwright/CDP 可用的 cookie 列表（域固定小红书）。"""
    out = []
    for seg in text.replace("\n", ";").split(";"):
        seg = seg.strip()
        if not seg or "=" not in seg:
            continue
        k, v = seg.split("=", 1)
        k, v = k.strip(), v.strip()
        if not k:
            continue
        out.append({
            "name": k,
            "value": v,
            "domain": ".xiaohongshu.com",
            "path": "/",
            "secure": True,
            "httpOnly": k in ("web_session", "last_web_session", "a1", "acw_tc"),
            "sameSite": "Lax",
        })
    return out


def _save_cookies_file(cookies: list[dict]):
    """备份旧文件后保存 cookies.json（供 profile 模式启动自动载入）。"""
    dest = BASE / "data" / "cookies.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.replace(dest.with_suffix(".json.bak"))
    dest.write_text(json.dumps(
        {"cookies": cookies, "imported_at": datetime.datetime.now().isoformat(timespec="seconds")},
        ensure_ascii=False, indent=2,
    ))


def _read_chrome_cookies() -> tuple[list | None, dict | None]:
    """读 .codex-xhs-profile 的 Chrome Cookie 库（钥匙串解密）。返回 (cookies, 错误响应)；成功时错误为 None。"""
    profile_dir = os.environ.get("XHS_PROFILE_DIR") or os.path.join(
        os.path.expanduser("~"), ".codex-xhs-profile"
    )
    os.environ["XHS_CHROME_USER_DATA_DIR"] = profile_dir
    from codex_xhs import cookie_reader

    try:
        return cookie_reader.read_chrome_cookies(), None
    except cookie_reader.ChromeCookieReadError as e:
        status = cookie_reader.keychain_status()
        if status == "pending":
            return None, {
                "ok": False,
                "need_auth": True,
                "error": "首次自动读取需要授权：请在弹出的系统钥匙串窗口中输入密码并点「始终允许」，然后重试。",
            }
        return None, {"ok": False, "need_auth": False, "error": f"读取 Chrome 登录态失败：{str(e)[:120]}"}


@app.post("/api/refresh_cookie_from_chrome")
async def refresh_cookie_from_chrome():
    """方案 A 自动续期：直接读日常 Chrome 的 Cookie 库（钥匙串解密）→ 校验 → 落盘。

    - 已授权：读库 → 过滤小红书 cookie → 转文本 → 走与手动导入相同的校验/保存链路；
    - 未授权（首次）：读取会触发系统钥匙串授权弹窗，返回 need_auth=True 引导用户点「始终允许」；
    - Chrome 库缺失 / 未登录小红书：返回可读错误，前端引导扫码或手动导入。
    """
    from codex_xhs import browser_client, cookie_reader

    # 统一独立 profile 浏览器架构：登录态来源 = 采集浏览器自身（.codex-xhs-profile）。
    # 读取其 Chrome Cookie 库（钥匙串解密，跨平台无 CDP 依赖），扫码登录落盘即闭环。
    chrome_cookies, err = _read_chrome_cookies()
    if chrome_cookies is None:
        return err

    if not chrome_cookies:
        return {"ok": False, "need_auth": False,
                "error": "Chrome 中未找到小红书 Cookie（可能未在小红书网页版登录），请先登录或手动导入 Cookie。"}

    cookies = _parse_cookie_text(cookie_reader.cookies_to_text(chrome_cookies))
    if not cookies:
        return {"ok": False, "need_auth": False, "error": "从 Chrome 解析 Cookie 失败"}

    r = await _verify_cookies_or_raise(cookies)

    if not r.get("logged_in"):
        return {
            "ok": False,
            "valid": False,
            "error": "Chrome 中的 Cookie 无效或已过期（登录态检测未通过）。请去 Chrome 扫码登录后重试，或手动导入 Cookie。",
            "count": len(cookies),
            "raw": f"url={r.get('url')} hasLoginUi={r.get('hasLoginUi')}",
        }

    _save_cookies_file(cookies)
    ws = next((c["value"] for c in cookies if c["name"] == "web_session"), "")
    return {
        "ok": True,
        "valid": True,
        "count": len(cookies),
        "account": (ws[:8] + "…") if ws else "已登录",
        "source": "chrome",
        "note": "已自动从 Chrome 读取登录态并保存（方案 A：钥匙串解密，无 CDP 依赖）。",
    }


async def _verify_cookies_or_raise(cookies: list) -> dict:
    """校验导入/读取的 Cookie（独立 profile Chrome），失败抛 502；后台窗口模式用后即停。"""
    from codex_xhs import browser_client

    try:
        return await browser_client.verify_cookies(cookies)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"校验失败（独立浏览器未就绪）：{str(e)[:100]}")
    finally:
        try:
            # 用后即停仅限后台窗口模式；演示（XHS_PROFILE_WINDOW=normal）保留窗口供用户查看
            if os.environ.get("XHS_PROFILE_WINDOW") != "normal":
                await browser_client.stop_profile()
        except Exception:  # noqa: BLE001
            pass


@app.post("/api/import_cookie")
async def import_cookie(req: ImportCookieReq):
    """导入 Cookie（新架构）：解析 → 注入独立 profile Chrome 校验登录态 → 有效则保存到本地。

    - daily 调试模式：校验在独立 profile Chrome（APP 交付形态预演）中完成，不污染用户日常 Chrome；
    - 生产模式：APP 自拉独立 profile，导入后直接注入该 Chrome，采集/发布即用该账号 —— 链路相同。
    - 校验失败（无效/过期）：返回原因，前端引导去 Chrome 扫码登录。
    """
    text = (req.cookie_text or "").strip()
    if not text:
        raise HTTPException(400, "请粘贴需要导入的 Cookie（name=value; 格式）")
    cookies = _parse_cookie_text(text)
    if not cookies:
        raise HTTPException(400, "未能从文本中解析出 Cookie（请确认格式：web_session=xxx; a1=yyy）")

    r = await _verify_cookies_or_raise(cookies)
    if not r.get("logged_in"):
        return {
            "ok": False,
            "valid": False,
            "error": "Cookie 无效或已过期（登录态检测未通过）。请确认 Cookie 来自最近登录的小红书账号，或直接去 Chrome 扫码登录。",
            "count": len(cookies),
            "raw": f"url={r.get('url')} hasLoginUi={r.get('hasLoginUi')}",
        }

    # 有效：备份旧文件后保存（供 profile 模式启动自动载入）
    _save_cookies_file(cookies)
    ws = next((c["value"] for c in cookies if c["name"] == "web_session"), "")
    return {
        "ok": True,
        "valid": True,
        "count": len(cookies),
        "account": (ws[:8] + "…") if ws else "已登录",
        "note": "Cookie 有效并已保存。独立浏览器（生产 APP 环境）将直接使用该账号；当前调试环境仍以日常 Chrome 登录态为准。",
    }


# ================= 发布 =================
class PublishReq(BaseModel):
    title: str
    content: str = ""
    desc: str = ""  # 兼容前端旧字段（apiPublish 传 desc）
    tags: list[str] = []
    visibility: str = "仅自己可见"
    images: list[str] = []
    feed_id: str = ""
    is_original: bool = False


@app.post("/api/save_draft")
async def save_draft(req: PublishReq):
    """存草稿（长期保留资产；与搜索/采集结果'用完即失效'区分，草稿可随时恢复）"""
    con = _db()
    con.execute(
        "INSERT INTO drafts (feed_id, title, content, tags, visibility, status, created_at) VALUES (?,?,?,?,?,?,?)",
        (req.feed_id or "", req.title[:50], (req.content or req.desc)[:200], ",".join(req.tags), req.visibility,
         "draft", _now().isoformat(timespec="seconds")),
    )
    con.commit()
    return {"ok": True, "note": "草稿已保存（长期保留，可在草稿库恢复）"}


# 发布拟人化护栏（2026-10-26 v3 修订：合理像人档，保留自动化）
#   依据 research/step5-小红书风控调研.md v3：
#   - 被抓根因=频率过高（凌晨连发+30s 重试），非"用工具即死"
#   - 合理像人：redbook 间隔 3-4h；MediaUse ≤3/h；真人创作者日更 1-3 条
#   - 不取 NoobClaw 1帖/天蜗牛档；也不取"随时连发"的危险档
#   - 所有动作过 human_delay() 随机延迟层，杜绝固定节奏
#   - 风控信号 → 硬熔断 24h（人工解锁），绝不自动重试
PUBLISH_COOLDOWN_MIN = 180          # 两次发布最小间隔（3h，redbook 下限）
PUBLISH_COOLDOWN_JITTER = 15        # 间隔随机抖动 ±15min，打散固定节奏
PUBLISH_DAILY_MAX = 2               # 每日成功发布上限（真人日更 1-3 条的保守侧）
PUBLISH_DAILY_MAX_STR = "2"         # 提示文案用
WEEKLY_MAX_DAYS = 6                 # 每周最多 6 个发布日（模拟真人每周有休息日）
ACTIVE_HOUR_START = 10              # 仅 10:00-21:30 真人活跃时段可发布
ACTIVE_HOUR_END = 21.5
DUPLICATE_WINDOW_HOURS = 24         # 同一标题+正文 24h 拒发
RISK_KEYWORDS = ("验证", "频繁", "风控", "拦截", "校验", "未跳转", "发布失败", "异常", "操作过快", "稍后再试")

# 风控熔断：最近 N 小时内出现过风控类失败 → 拒绝一切发布（硬停，需人工解锁）
RISK_BLOCK_HOURS = 24


def human_delay(lo: float = 1.0, hi: float = 3.0):
    """拟人化随机延迟：所有碰账号的动作在调用前先"思考"随机时长，杜绝固定节奏。"""
    time.sleep(random.uniform(lo, hi))


def _risk_blocked_until(con) -> str | None:
    """最近 24h 内发布失败且命中风控关键词 → 返回熔断到期时间，否则 None。"""
    since = (_now() - datetime.timedelta(hours=RISK_BLOCK_HOURS)).isoformat(timespec="seconds")
    rows = con.execute(
        "SELECT raw, created_at FROM publish_logs WHERE status='failed' AND created_at>=? ORDER BY id DESC LIMIT 5",
        (since,),
    ).fetchall()
    for row in rows:
        if any(kw in (row["raw"] or "") for kw in RISK_KEYWORDS):
            until = datetime.datetime.fromisoformat(row["created_at"]) + datetime.timedelta(hours=RISK_BLOCK_HOURS)
            if until > _now():
                return until.isoformat(timespec="minutes")
    return None


def _publish_week_days(con) -> int:
    """近 7 天有成功发布的日期数（模拟每周休息日）。"""
    since = (_now() - datetime.timedelta(days=7)).isoformat(timespec="seconds")
    row = con.execute(
        "SELECT COUNT(DISTINCT date(created_at)) AS c FROM publish_logs WHERE status='success' AND created_at>=?",
        (since,),
    ).fetchone()
    return row["c"] if row else 0


def _now():
    return datetime.datetime.now()


def _last_publish_ago_minutes(con) -> float | None:
    row = con.execute("SELECT created_at FROM publish_logs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    try:
        last = datetime.datetime.fromisoformat(row["created_at"])
    except (TypeError, ValueError):
        return None
    return (_now() - last).total_seconds() / 60.0


def _publish_count_today(con) -> int:
    today = _now().date().isoformat()
    row = con.execute(
        "SELECT COUNT(*) AS c FROM publish_logs WHERE status='success' AND date(created_at)=?",
        (today,),
    ).fetchone()
    return row["c"] if row else 0


def _duplicate_published(con, title, content) -> bool:
    since = (_now() - datetime.timedelta(hours=DUPLICATE_WINDOW_HOURS)).isoformat(timespec="seconds")
    row = con.execute(
        "SELECT COUNT(*) AS c FROM publish_logs WHERE status='success' AND title=? AND content=? AND created_at>=?",
        (title[:50], content[:200], since),
    ).fetchone()
    return (row["c"] if row else 0) > 0


def _publish_guard_time(con) -> dict | None:
    """护栏（时间类）：风控熔断 / 活跃时段 / 冷却间隔。任一不满足返回拒绝响应。"""
    blocked = _risk_blocked_until(con)
    if blocked:
        return {
            "ok": False,
            "cooldown": True,
            "raw": f"风控熔断中：近 24h 发布被平台拦截（验证码/频繁/拦截）。发布功能已自动停用至 {blocked}，请人工确认账号状态后再恢复。切勿自动重试。",
        }

    hour = _now().hour + _now().minute / 60.0
    if not (ACTIVE_HOUR_START <= hour <= ACTIVE_HOUR_END):
        return {
            "ok": False,
            "cooldown": True,
            "raw": f"当前 {_now().hour:02d}:{_now().minute:02d} 非真人活跃时段（仅 {ACTIVE_HOUR_START}:00-21:30 可发布）。真人不会半夜发笔记，请在白天发布。",
        }

    ago = _last_publish_ago_minutes(con)
    limit = PUBLISH_COOLDOWN_MIN + random.uniform(0, PUBLISH_COOLDOWN_JITTER)
    if ago is not None and ago < limit:
        wait = limit - ago
        return {
            "ok": False,
            "cooldown": True,
            "raw": f"发布过于频繁：距上次发布仅 {ago:.0f} 分钟。平台对短时间连发很敏感（间隔 3-4 小时），请等待 {wait:.0f} 分钟后重试。",
        }
    return None


def _publish_guard_quota(con, req: PublishReq) -> dict | None:
    """护栏（配额类）：每日上限 / 每周发布日 / 文案防重。任一不满足返回拒绝响应。"""
    if _publish_count_today(con) >= PUBLISH_DAILY_MAX:
        return {
            "ok": False,
            "cooldown": True,
            "raw": f"今日发布已达上限（{PUBLISH_DAILY_MAX} 条）。真人日更 1-3 条，明天再发。",
        }

    if _publish_week_days(con) >= WEEKLY_MAX_DAYS:
        return {
            "ok": False,
            "cooldown": True,
            "raw": "本周已连续发布 6 天。真人每周会有休息日，请明天再发。",
        }

    if _duplicate_published(con, req.title, req.content or req.desc):
        return {
            "ok": False,
            "cooldown": True,
            "raw": f"该文案 {DUPLICATE_WINDOW_HOURS} 小时内已发布过。平台对重复文案判定极严，请改写标题或正文。",
        }
    return None


def _publish_guard(con, req: PublishReq) -> dict | None:
    """发布护栏链：时间类 + 配额类检查。任一不满足返回拒绝响应，否则 None。"""
    return _publish_guard_time(con) or _publish_guard_quota(con, req)


@app.post("/api/publish")
def publish(req: PublishReq):
    # ① 拟人思考：发布前随机停顿 2-5s（像真人斟酌后再点）
    human_delay(2.0, 5.0)

    con = _db()
    guard = _publish_guard(con, req)
    if guard:
        return guard

    # ⑧ 执行发布：Docker 已退役（2026-10-26 迁移：采集/登录迁到外部 Chrome）。
    #    发布永远用户手动（产品红线）：后端只做校验与载荷准备，实际发布由用户在
    #    Chrome 中手动完成（避免自动化发布触发平台风控）。载荷已随请求完整传给前端。
    import urllib.parse
    xhs_write_url = "https://www.xiaohongshu.com/write"
    ok = False
    text = "发布执行已迁移：请在 Chrome 小红书发布页手动发布（标题/正文/图片已就绪）。" \
           f"发布页: {xhs_write_url}"

    con.execute(
        "INSERT INTO publish_logs (feed_id, title, content, tags, visibility, status, created_at) VALUES (?,?,?,?,?,?,?)",
        (req.feed_id, req.title[:50], (req.content or req.desc)[:200], ",".join(req.tags), req.visibility,
         "success" if ok else "failed", _now().isoformat(timespec="seconds")),
    )
    con.commit()
    return {"ok": ok, "raw": text[:400]}


# ================= 发布记录 =================
@app.get("/api/logs")
def logs():
    con = _db()
    rows = con.execute("SELECT * FROM publish_logs ORDER BY id DESC LIMIT 50").fetchall()
    return {"logs": [dict(r) for r in rows]}


# ================= 图片代理（容器内路径 → 浏览器可读） =================
@app.get("/api/img")
def img_proxy(path: str = ""):
    """把 /app/images/<file> 映射到本机 data/images/<file> 返回图片字节。"""
    from fastapi.responses import Response
    if not path or not path.startswith("/app/images/"):
        raise HTTPException(400, "仅允许 /app/images 路径")
    fname = Path(path).name  # 只取文件名，防路径穿越
    f = IMAGES_DIR / fname
    if not f.exists():
        raise HTTPException(404, "图片不存在")
    return Response(f.read_bytes(), media_type="image/jpeg")


# ================= 静态前端 =================
app.mount("/assets", StaticFiles(directory=str(WEB_DIR / "assets")), name="assets")
# 本地封面图静态服务（xhscdn 图床按 Referer 防盗链，本地代理同源访问无 403）
app.mount("/app/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")
