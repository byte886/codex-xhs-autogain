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
import json
import random
import sqlite3
import time
import urllib.request
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---- 路径 ----
BASE = Path(__file__).resolve().parents[3]  # ~/Desktop/codex-zhuke（server.py 在 app/src/codex_xhs/ 下）
DB_PATH = BASE / "data" / "xhs_notes.db"
WEB_DIR = BASE / "app" / "web"
IMAGES_DIR = BASE / "docker" / "images"

MCP_URL = "http://localhost:18060/mcp"
_INTERVAL = (1.0, 1.8)

from codex_xhs.score import rank_notes  # noqa: E402
from codex_xhs.collect_pipeline import (  # noqa: E402
    get_feed_detail,
    get_user_profile,
    mcp_call,
    parse_feeds,
)

app = FastAPI(title="Codex 自动获客 · 本地工具")

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
IMAGES_DIR.mkdir(parents=True, exist_ok=True)


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
    con.commit()


# ================= 登录 =================
@app.get("/api/login_status")
def login_status():
    res = mcp_call("check_login_status", {})
    text = res.get("_raw_text") or json.dumps(res, ensure_ascii=False)
    return {"status": "logged_in" if "已登录" in text else "not_logged_in", "raw": text[:200]}


# ================= 搜索采集 =================
class SearchReq(BaseModel):
    keyword: str
    limit: int = 20


@app.post("/api/search")
def search(req: SearchReq):
    """搜索 → 详情+粉丝补全 → 评分 → 落库。返回新增条数与最新 TOP 评分。"""
    con = _db()
    before = con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    res = mcp_call("search_feeds", {"keyword": req.keyword, "filters": {"sort_by": "综合"}})
    notes = parse_feeds(res)[: req.limit]
    if not notes:
        return {"added": 0, "total": before, "top": [], "note": "该关键词本轮返回 0 条（可能被限流，可稍后重试）"}

    cache = {}
    saved = 0
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for n in notes:
        try:
            n["keyword"] = req.keyword
            n = get_feed_detail(n)
            n = get_user_profile(n, cache)
            si = rank_notes([n])[0]["score_info"]
            con.execute(
                "INSERT OR REPLACE INTO notes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    n.get("feed_id"), req.keyword, n.get("title", ""), n.get("type", ""),
                    n.get("user_id"), n.get("nickname", ""), n.get("avatar", ""),
                    n.get("cover_url", ""), n.get("xsec_token", ""), n.get("desc", ""),
                    n.get("ip_location", ""),
                    int(n.get("liked_count") or 0), int(n.get("collected_count") or 0),
                    int(n.get("comment_count") or 0), int(n.get("shared_count") or 0),
                    int(n.get("fans_count") or 0), n.get("publish_time"),
                    si["score"], si["tag"], si["fan_ratio"], now,
                ),
            )
            saved += 1
        except Exception:
            continue
        time.sleep(_INTERVAL[0] + random.random() * (_INTERVAL[1] - _INTERVAL[0]))
    con.commit()

    top = con.execute(
        "SELECT feed_id, title, nickname, liked_count, fans_count, score, tag, cover_url "
        "FROM notes ORDER BY score DESC LIMIT 10"
    ).fetchall()
    return {
        "added": saved,
        "total": con.execute("SELECT COUNT(*) FROM notes").fetchone()[0],
        "top": [dict(r) for r in top],
    }


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
    return {"total": len(rows), "notes": [dict(r) for r in rows]}


# ================= 单条详情 =================
@app.get("/api/note/{feed_id}")
def note_detail(feed_id: str):
    con = _db()
    r = con.execute("SELECT * FROM notes WHERE feed_id=?", (feed_id,)).fetchone()
    if not r:
        raise HTTPException(404, "笔记不存在")
    n = dict(r)
    # 尝试用 token 拉最新详情（含图片列表）
    try:
        res = mcp_call("get_feed_detail", {"feed_id": feed_id, "xsec_token": n.get("xsec_token", "")})
        detail = res.get("data", {}).get("note", {})
        if detail:
            imgs = [i.get("urlDefault") or i.get("url") for i in detail.get("imageList", [])]
            imgs = [u for u in imgs if u]
            n["images"] = imgs
            if detail.get("desc"):
                n["desc"] = detail.get("desc")
    except Exception:
        pass
    return n


# ================= 一键克隆 =================
class CloneReq(BaseModel):
    feed_id: str


@app.post("/api/clone")
def clone(req: CloneReq):
    """克隆：取详情 → 下载图片到 docker/images → 返回可编辑载荷。"""
    con = _db()
    r = con.execute("SELECT * FROM notes WHERE feed_id=?", (req.feed_id,)).fetchone()
    if not r:
        raise HTTPException(404, "笔记不存在")
    n = dict(r)

    # 图片列表（优先详情接口，其次封面）
    images = []
    detail_ok = False
    try:
        res = mcp_call("get_feed_detail", {"feed_id": req.feed_id, "xsec_token": n.get("xsec_token", "")})
        detail = res.get("data", {}).get("note", {})
        if detail:
            detail_ok = True
            imgs = [i.get("urlDefault") or i.get("url") for i in detail.get("imageList", [])]
            images = [u for u in imgs if u]
            if detail.get("desc"):
                n["desc"] = detail.get("desc")
    except Exception:
        pass
    if not images and n.get("cover_url"):
        images = [n["cover_url"]]

    local_images = []
    for idx, u in enumerate(images[:9]):
        fname = f"{req.feed_id}_{idx}.jpg"
        dest = IMAGES_DIR / fname
        try:
            if not dest.exists():
                reqh = urllib.request.Request(u, headers={"Referer": "https://www.xiaohongshu.com/", "User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(reqh, timeout=20) as resp:
                    dest.write_bytes(resp.read())
            local_images.append(f"/app/images/{fname}")
        except Exception as e:
            local_images.append("")  # 下载失败留空，前端提示

    n["images_local"] = local_images
    n["detail_ok"] = detail_ok
    return n


# ================= 发布 =================
class PublishReq(BaseModel):
    title: str
    content: str
    tags: list[str] = []
    visibility: str = "仅自己可见"
    images: list[str] = []
    feed_id: str = ""
    is_original: bool = False


@app.post("/api/publish")
def publish(req: PublishReq):
    res = mcp_call("publish_content", {
        "title": req.title[:20],
        "content": req.content,
        "tags": req.tags,
        "visibility": req.visibility,
        "images": [i for i in req.images if i],
        "is_original": req.is_original,
    }, timeout=120)
    text = res.get("_raw_text") or json.dumps(res, ensure_ascii=False)
    ok = "发布成功" in text or "内容发布成功" in text

    con = _db()
    con.execute(
        "INSERT INTO publish_logs (feed_id, title, content, tags, visibility, status, created_at) VALUES (?,?,?,?,?,?,?)",
        (req.feed_id, req.title[:50], req.content[:200], ",".join(req.tags), req.visibility,
         "success" if ok else "failed", datetime.datetime.now().isoformat(timespec="seconds")),
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
    """把 /app/images/<file> 映射到本机 docker/images/<file> 返回图片字节。"""
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


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")
