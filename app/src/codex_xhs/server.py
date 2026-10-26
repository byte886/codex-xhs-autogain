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
from concurrent.futures import ThreadPoolExecutor
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

_INTERVAL = (1.0, 1.8)

from codex_xhs.score import rank_notes  # noqa: E402
from codex_xhs.collect_pipeline import (  # noqa: E402
    get_feed_detail,
    get_user_profile,
    search_via_browser,
)

app = FastAPI(title="Codex 自动获客 · 本地工具")

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
    con.commit()


# ================= 登录 =================
# 登录态缓存：Chrome CDP 连接会弹「远程调试授权」窗（每次连接必弹，官方无法关闭）。
# 前端轮询 login_status 若每次都连 Chrome → 每几秒弹一次窗（用户痛点「关了又开」）。
# 因此缓存检测结果 TTL=30s，轮询打缓存，不反复连 Chrome。
_LOGIN_CACHE = {"t": 0.0, "data": None}
_LOGIN_CACHE_TTL = 30.0

@app.get("/api/login_status")
async def login_status():
    """本地浏览器登录态检测：经常驻 Playwright 连接（复用 Chrome 连接，不反复弹授权窗）。结果缓存 30s。"""
    import time

    now = time.monotonic()
    if _LOGIN_CACHE["data"] is not None and now - _LOGIN_CACHE["t"] < _LOGIN_CACHE_TTL:
        return _LOGIN_CACHE["data"]

    try:
        from codex_xhs import browser_client
        st = await browser_client.login_check()
        ok = st.get("logged_in")
        result = {
            "status": "logged_in" if ok else "not_logged_in",
            "logged_in": bool(ok),
            "nickname": "",
            "raw": f"url={st.get('url')} noteCards={st.get('noteCards')}",
        }
    except Exception as e:
        result = {"status": "unknown", "logged_in": False, "nickname": "", "raw": f"本地浏览器检测失败: {str(e)[:100]}"}

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


@app.post("/api/search")
async def search(req: SearchReq):
    """搜索 → 详情+粉丝补全 → 评分 → 落库。返回新增条数与最新 TOP 评分。"""
    con = _db()
    before = con.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    source = "关键词搜索（本地 Chrome）"
    search_err = ""
    try:
        # 本地浏览器搜索：Playwright 控制外部 Chrome（真实设备/真实登录态），DOM 层提取
        notes = await search_via_browser(req.keyword, req.limit)
    except Exception as e:
        notes = []
        search_err = str(e)
    if not notes:
        return {"added": 0, "total": before, "top": [], "note": f"搜索未返回结果（{search_err[:80]}），请确认 Chrome 已打开并登录小红书后重试"}

    cache = {}
    saved = 0
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for n in notes:
        try:
            n["keyword"] = req.keyword
            # 轻量采集 v2：列表字段直接入库，不逐条抓详情/作者主页
            #（每条详情+主页 ≈ 15s，20 条要 6 分钟；详情/作者在「一键克隆」时按需补全）
            # 封面图采集时下载到本地（xhscdn 防盗链，浏览器直连会失败；失败不阻断落库）
            cover_local = ""
            # 封面下载移出循环（循环后并发批量下载，网络 IO 并行不占拟人化节奏）
            n["local_cover"] = ""
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

    # 封面并发下载（网络 IO 并行，不占拟人化请求节奏；已存在跳过）
    def _dl_cover(n):
        if not n.get("cover_url"):
            return
        cf = IMAGES_DIR / _img_name(n["feed_id"])
        if cf.exists():
            return
        try:
            reqh = urllib.request.Request(
                n["cover_url"],
                headers={"Referer": "https://www.xiaohongshu.com/", "User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(reqh, timeout=10) as resp:
                cf.write_bytes(resp.read())
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(_dl_cover, notes))

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
    out = []
    for r in rows:
        d = dict(r)
        # 本地封面存在则替换为本地代理路径（xhscdn 防盗链，直连会 403）
        cf = IMAGES_DIR / _img_name(d["feed_id"])
        d["local_cover"] = f"/app/images/{_img_name(d['feed_id'])}" if cf.exists() else ""
        out.append(d)
    return {"total": len(out), "notes": out}


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
    con = _db()
    r = con.execute("SELECT * FROM notes WHERE feed_id=?", (req.feed_id,)).fetchone()
    if not r:
        raise HTTPException(404, "笔记不存在")
    n = dict(r)

    # 图片列表（优先本地浏览器详情提取，其次封面）
    images = []
    detail_ok = False
    try:
        dn = dict(n)
        dn = await get_feed_detail(dn)
        imgs = dn.get("detail_imgs") or []
        if imgs:
            detail_ok = True
            images = imgs
            if dn.get("desc"):
                n["desc"] = dn.get("desc")
    except Exception:
        pass
    if not images and n.get("cover_url"):
        images = [n["cover_url"]]

    local_images = []
    def _dl_one(idx_url):
        idx, u = idx_url
        fname = _img_name(req.feed_id, suffix=f"p{idx}")
        dest = IMAGES_DIR / fname
        try:
            if not dest.exists():
                reqh = urllib.request.Request(u, headers={"Referer": "https://www.xiaohongshu.com/", "User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(reqh, timeout=20) as resp:
                    dest.write_bytes(resp.read())
            return f"/app/images/{fname}"
        except Exception:
            return ""
    with ThreadPoolExecutor(max_workers=6) as ex:
        local_images = list(ex.map(_dl_one, enumerate(images[:9])))

    n["images_local"] = local_images
    n["detail_ok"] = detail_ok
    return n


# ================= Cookie（显示 / 一键从 Chrome 导入） =================
_COOKIE_ORDER = ["web_session", "last_web_session", "a1", "webId", "gid", "id_token", "acw_tc", "websectiga"]


@app.get("/api/cookie_info")
async def cookie_info():
    """从外部 Chrome 实时读取小红书 cookie 摘要（界面「显示 Cookie」只读展示用）。
    新架构下采集/登录都在同一个 Chrome 内完成，无跨环境搬运，故只读展示。经常驻 Playwright 连接。"""
    try:
        from codex_xhs import browser_client
        d = await browser_client.cookies()
        names = d.get("names", [])
        items = []
        for i, nm in enumerate(names):
            items.append({"name": nm, "value": "", "domain": ".xiaohongshu.com", "ord": i})
        items.sort(key=lambda x: (_COOKIE_ORDER.index(x["name"]) if x["name"] in _COOKIE_ORDER else 99, x["name"]))
        ws = d.get("web_session") or ""
        return {
            "ok": True,
            "count": d.get("count", 0),
            "saved_at": "",
            "account": (ws[:8] + "…") if ws else "",
            "cookies": items,
        }
    except Exception as e:
        return {"ok": False, "count": 0, "saved_at": "", "cookies": [], "error": f"本地浏览器读取失败: {str(e)[:100]}"}


@app.post("/api/import_cookie")
def import_cookie():
    """导入 Cookie 功能已下线：新架构下采集/登录同在外部 Chrome（同环境），
    无跨环境搬运需求；界面 Cookie 区保持只读实时展示。"""
    return {"ok": False, "error": "导入功能已下线（采集与登录同在 Chrome 内，直接读取无需导入）"}


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
# 本地封面图静态服务（xhscdn 图床按 Referer 防盗链，本地代理同源访问无 403）
app.mount("/app/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")
