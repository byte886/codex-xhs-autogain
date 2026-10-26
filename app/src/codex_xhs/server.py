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
    # v3 拟人化护栏需要 raw 列（风控熔断关键词检测）；老库补列
    try:
        con.execute("ALTER TABLE publish_logs ADD COLUMN raw TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在
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
    source = "关键词搜索"
    try:
        # 搜索被限时容器侧固定 60s panic；单次 65s 超时、不重试，快速降级避免拖死浏览器
        res = mcp_call("search_feeds", {"keyword": req.keyword, "filters": {"sort_by": "综合"}}, timeout=65, attempts=1)
        notes = parse_feeds(res)[: req.limit]
    except Exception:
        notes = []
    if not notes:
        # 搜索接口被平台限流（无头浏览器高频搜索触发）→ 自动降级首页推荐流，
        # 保证采集链路不中断（首页流未受限，实测可用）。
        try:
            res = mcp_call("list_feeds", {}, timeout=90, attempts=2)
            notes = parse_feeds(res)[: req.limit]
            source = "首页推荐流（搜索被限，自动降级）"
        except Exception:
            return {"added": 0, "total": before, "top": [], "note": "搜索与推荐流均不可用（可能被限流，可稍后重试）"}
    if not notes:
        return {"added": 0, "total": before, "top": [], "note": "该关键词本轮返回 0 条（可能被限流，已尝试推荐流降级），可稍后重试"}

    cache = {}
    saved = 0
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for n in notes:
        try:
            n["keyword"] = req.keyword
            try:
                n = get_feed_detail(n)          # 详情失败→用列表字段兜底
            except Exception:
                pass
            try:
                n = get_user_profile(n, cache)  # 作者失败→粉丝缺失，评分降级
            except Exception:
                pass
            try:
                si = rank_notes([n])[0]["score_info"]
            except Exception:
                si = {"score": 0.0, "tag": "评分失败", "fan_ratio": 0.0}
            con.execute(
                "INSERT OR REPLACE INTO notes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
        "source": source,
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


@app.post("/api/publish")
def publish(req: PublishReq):
    # ① 拟人思考：发布前随机停顿 2-5s（像真人斟酌后再点）
    human_delay(2.0, 5.0)

    con = _db()

    # ② 风控熔断：近 24h 有风控类失败 → 硬停，人工解锁
    blocked = _risk_blocked_until(con)
    if blocked:
        return {
            "ok": False,
            "cooldown": True,
            "raw": f"风控熔断中：近 24h 发布被平台拦截（验证码/频繁/拦截）。发布功能已自动停用至 {blocked}，请人工确认账号状态后再恢复。切勿自动重试。",
        }

    # ③ 时段限制：仅真人活跃时段（10:00-21:30）可发布
    hour = _now().hour + _now().minute / 60.0
    if not (ACTIVE_HOUR_START <= hour <= ACTIVE_HOUR_END):
        return {
            "ok": False,
            "cooldown": True,
            "raw": f"当前 {_now().hour:02d}:{_now().minute:02d} 非真人活跃时段（仅 {ACTIVE_HOUR_START}:00-21:30 可发布）。真人不会半夜发笔记，请在白天发布。",
        }

    # ④ 冷却检查：距上次发布不足 N±抖动 分钟 → 拒绝
    ago = _last_publish_ago_minutes(con)
    limit = PUBLISH_COOLDOWN_MIN + random.uniform(0, PUBLISH_COOLDOWN_JITTER)
    if ago is not None and ago < limit:
        wait = limit - ago
        return {
            "ok": False,
            "cooldown": True,
            "raw": f"发布过于频繁：距上次发布仅 {ago:.0f} 分钟。平台对短时间连发很敏感（间隔 3-4 小时），请等待 {wait:.0f} 分钟后重试。",
        }

    # ⑤ 每日上限：成功发布数已达上限 → 拒绝
    if _publish_count_today(con) >= PUBLISH_DAILY_MAX:
        return {
            "ok": False,
            "cooldown": True,
            "raw": f"今日发布已达上限（{PUBLISH_DAILY_MAX} 条）。真人日更 1-3 条，明天再发。",
        }

    # ⑥ 每周发布日 ≤6：模拟每周至少休息一天
    if _publish_week_days(con) >= WEEKLY_MAX_DAYS:
        return {
            "ok": False,
            "cooldown": True,
            "raw": "本周已连续发布 6 天。真人每周会有休息日，请明天再发。",
        }

    # ⑦ 文案防重：同一标题+正文 24h 内已成功发布 → 拒绝
    if _duplicate_published(con, req.title, req.content):
        return {
            "ok": False,
            "cooldown": True,
            "raw": f"该文案 {DUPLICATE_WINDOW_HOURS} 小时内已发布过。平台对重复文案判定极严，请改写标题或正文。",
        }

    # ⑧ 执行发布（MCP 容器内部节奏无法注入，靠调用频率与时机拟人化）
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

    con.execute(
        "INSERT INTO publish_logs (feed_id, title, content, tags, visibility, status, created_at) VALUES (?,?,?,?,?,?,?)",
        (req.feed_id, req.title[:50], req.content[:200], ",".join(req.tags), req.visibility,
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
