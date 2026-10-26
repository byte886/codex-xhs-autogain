"""M3/M4: 采集 + 评分 + 落库流水线（对 xiaohongshu-mcp HTTP MCP 的封装）。

用法:
  python3 collect_pipeline.py "翡翠" [--limit 50] [--out /path/db.sqlite3]

流程:
  1. search_feeds 搜索关键词 → 解析 feed 卡片（互动数据 + xsec_token）
  2. 对每条候选调 get_feed_detail 拿发布时间 + user_profile 拿粉丝数（按作者缓存）
  3. score.rank_notes 评分排序
  4. 落 SQLite（notes 表 + 评分字段）
"""
import argparse
import json
import random
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

# 允许从任意 cwd 导入 score 模块
sys.path.insert(0, str(Path(__file__).resolve().parent))
from codex_xhs.score import rank_notes  # noqa: E402

MCP_URL = "http://localhost:18060/mcp"
REQUEST_INTERVAL = (1.0, 1.8)  # 真人浏览节奏：请求间随机 1-1.8s（用户拍板合理像人档，勿再调快）
SESSION_MAX_NOTES = 20        # 单次采集会话最多 20 条（真人一次浏览的量级，勿一次拉太多）


def mcp_call(tool: str, args: dict, timeout: int = 90, attempts: int = 3) -> dict:
    """调用 MCP 工具，返回 result.content[0].text 解析后的对象。断连/超时退避重试 attempts 次。"""
    body = {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000) % 100000,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }
    last_err = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(
                MCP_URL,
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode()
            # 可能为 SSE 或纯 JSON
            if raw.startswith("event:"):
                for line in raw.splitlines():
                    if line.startswith("data:"):
                        raw = line[5:].strip()
                        break
            j = json.loads(raw)
            if "result" not in j:
                raise RuntimeError(f"{tool} 调用失败: {j}")
            content = j["result"]["content"]
            text = content[0]["text"]
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"_raw_text": text}
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{tool} 重试 3 次仍失败: {last_err}")


def parse_feeds(search_result: dict) -> list[dict]:
    """从 search_feeds 返回里提取笔记卡片列表。"""
    feeds = search_result.get("feeds", [])
    notes = []
    for f in feeds:
        if f.get("modelType") != "note":
            continue
        nc = f.get("noteCard", {})
        user = nc.get("user", {})
        inter = nc.get("interactInfo", {})
        notes.append({
            "feed_id": f.get("id"),
            "xsec_token": f.get("xsecToken"),
            "title": nc.get("displayTitle", ""),
            "type": nc.get("type", ""),
            "user_id": user.get("userId"),
            "nickname": user.get("nickname", ""),
            "avatar": user.get("avatar", ""),
            "liked_count": inter.get("likedCount", "0"),
            "shared_count": inter.get("sharedCount", "0"),
            "comment_count": inter.get("commentCount", "0"),
            "collected_count": inter.get("collectedCount", "0"),
            "cover_url": nc.get("cover", {}).get("urlPre") or nc.get("cover", {}).get("urlDefault", "") or nc.get("cover", {}).get("url", ""),
        })
    return notes


def _parse_likes(s: str) -> int:
    """解析互动数：'151'→151, '1.2万'→12000, '999+'→999"""
    if not s:
        return 0
    s = s.strip().replace(",", "")
    m = re.match(r"([\d.]+)(万|w|W)?", s)
    if not m:
        return 0
    v = float(m.group(1))
    if m.group(2):
        v *= 10000
    return int(v)


def search_via_browser(keyword: str, limit: int = 20) -> list[dict]:
    """本地浏览器搜索（替代容器 search_feeds）：
    通过 CDP 控制外部 Chrome（真实设备/真实登录态），DOM 层提取卡片。

    返回与 parse_feeds 相同结构的 notes 列表（feed_id/xsec_token/title/nickname/cover_url/liked_count）。
    """
    import subprocess

    root = Path(__file__).resolve().parents[3]  # app/src/codex_xhs → 项目根
    r = subprocess.run(
        ["node", "scripts/browser/actions.js", "search", keyword],
        cwd=root, capture_output=True, timeout=150,
    )
    out = (r.stdout or b"").decode(errors="replace")
    m = re.search(r"SEARCH:(\{.*\})", out, re.S)
    if not m:
        raise RuntimeError(f"本地浏览器搜索无输出: {(r.stderr or b'').decode(errors='replace')[:120] or out[:120]}")
    data = json.loads(m.group(1))
    items = data.get("items", [])[:limit]
    notes = []
    for it in items:
        href = it.get("href") or ""
        xsec = ""
        mm = re.search(r"xsec_token=([^&]+)", href)
        if mm:
            xsec = mm.group(1)
        # author 形如 "昵称\n09-22"，去掉日期
        author = (it.get("author") or "").replace("\n", " ")
        nickname = re.sub(r"\s\d{2}-\d{2}$", "", author).strip() or author
        notes.append({
            "feed_id": it.get("id"),
            "xsec_token": xsec,
            "title": it.get("title", ""),
            "type": "",
            "user_id": None,
            "nickname": nickname,
            "avatar": "",
            "liked_count": _parse_likes(it.get("likes", "")),
            "shared_count": 0,
            "comment_count": 0,
            "collected_count": 0,
            "cover_url": it.get("img", ""),
        })
    return notes


def _run_browser(cmd: str, arg: str, tag: str, arg2: str = "") -> dict | None:
    """调本地浏览器执行器（node scripts/browser/actions.js <cmd> <arg> [<arg2>]），
    返回 stdout 中 `<TAG>:<json>` 的 dict；失败返回 None。"""
    import subprocess

    root = Path(__file__).resolve().parents[3]
    try:
        argv = ["node", "scripts/browser/actions.js", cmd, arg] + ([arg2] if arg2 else [])
        r = subprocess.run(argv, cwd=root, capture_output=True, timeout=90)
        out = (r.stdout or b"").decode(errors="replace")
        m = re.search(rf"{tag}:(\{{.*\}})", out, re.S)
        if not m:
            return None
        return json.loads(m.group(1))
    except Exception:
        return None


def get_feed_detail(note: dict) -> dict:
    """补全笔记详情（本地浏览器打开笔记页 DOM 提取标题/正文/作者/日期/图片）。

    替代容器 get_feed_detail；无签名依赖，直接读渲染后的页面。
    """
    d = _run_browser("note", note.get("feed_id", ""), "NOTE", note.get("xsec_token", ""))
    if d:
        if d.get("title"):
            note["title"] = d["title"]
        if d.get("desc"):
            note["desc"] = d["desc"]
        # 无标题笔记兜底：取正文去掉话题标记后的首段前 20 字
        if not note.get("title") and note.get("desc"):
            dd = note["desc"].strip().lstrip("#").strip()
            dd = re.sub(r"\[.*?\]", "", dd).strip()
            note["title"] = dd[:20]
        note["publish_time"] = None  # DOM 无毫秒时间戳；日期在 author 文本（如 09-22）
        note["detail_imgs"] = d.get("imgs", [])
    time.sleep(REQUEST_INTERVAL[0] + random.random() * (REQUEST_INTERVAL[1] - REQUEST_INTERVAL[0]))
    return note


def get_user_profile(note: dict, cache: dict) -> dict:
    """拿作者粉丝数（本地浏览器打开作者主页 DOM 提取；按 user_id 缓存）。"""
    uid = note.get("user_id")
    if not uid:
        return note
    if uid in cache:
        note["fans_count"] = cache[uid]
        return note
    u = _run_browser("user", uid, "USER")
    fans = 0
    if u:
        f = u.get("fans") or ""
        if f:
            m = re.match(r"([\d.]+)(万|w|W)?", f.replace(",", ""))
            if m:
                fans = int(float(m.group(1)) * 10000 if m.group(2) else float(m.group(1)))
        if not fans and u.get("nickname"):
            note["nickname"] = u["nickname"]
    cache[uid] = fans
    note["fans_count"] = fans
    time.sleep(REQUEST_INTERVAL[0] + random.random() * (REQUEST_INTERVAL[1] - REQUEST_INTERVAL[0]))
    return note


def init_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            feed_id TEXT PRIMARY KEY,
            keyword TEXT,
            title TEXT,
            note_type TEXT,
            user_id TEXT,
            nickname TEXT,
            avatar TEXT,
            cover_url TEXT,
            xsec_token TEXT,
            desc TEXT,
            ip_location TEXT,
            liked_count INTEGER,
            collected_count INTEGER,
            comment_count INTEGER,
            shared_count INTEGER,
            fans_count INTEGER,
            publish_time INTEGER,
            score REAL,
            tag TEXT,
            fan_ratio REAL,
            fetched_at TEXT
        )
    """)
    return con


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("keywords", nargs="+", help="搜索关键词，可多个（如 翡翠 玉镯）")
    parser.add_argument("--limit", type=int, default=SESSION_MAX_NOTES, help=f"单会话采集上限（默认 {SESSION_MAX_NOTES}，真人浏览节奏）")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "data" / "xhs_notes.db"))
    args = parser.parse_args()

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    con = init_db(Path(args.out))

    print(f"[1/4] 多关键词搜索: {args.keywords}")
    all_notes = []
    seen = set()
    for kw in args.keywords:
        try:
            notes_kw = search_via_browser(kw, args.limit)
            for n in notes_kw:
                if n["feed_id"] not in seen:
                    seen.add(n["feed_id"])
                    n["keyword"] = kw
                    all_notes.append(n)
            print(f"      「{kw}」→ +{len(notes_kw)} 条，累计去重 {len(all_notes)} 条")
        except Exception as e:
            print(f"      「{kw}」失败: {e}")
        time.sleep(REQUEST_INTERVAL[0] + random.random() * (REQUEST_INTERVAL[1] - REQUEST_INTERVAL[0]))
    notes = all_notes[: args.limit]
    print(f"      → 最终取 {len(notes)} 条（去重后）")

    import datetime
    now = datetime.datetime.now().isoformat(timespec="seconds")

    def upsert(n):
        si = n["score_info"]
        con.execute(
            "INSERT OR REPLACE INTO notes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                n.get("feed_id"), n.get("keyword", args.keywords[0]), n.get("title", ""), n.get("type", ""),
                n.get("user_id"), n.get("nickname", ""), n.get("avatar", ""),
                n.get("cover_url", ""), n.get("xsec_token", ""), n.get("desc", ""), n.get("ip_location", ""),
                int(n.get("liked_count") or 0), int(n.get("collected_count") or 0),
                int(n.get("comment_count") or 0), int(n.get("shared_count") or 0),
                int(n.get("fans_count") or 0), n.get("publish_time"),
                si["score"], si["tag"], si["fan_ratio"], now,
            ),
        )
        con.commit()

    # 断点续跑：已落库的 feed_id 跳过
    existing = {r[0] for r in con.execute("SELECT feed_id FROM notes").fetchall()}
    todo = [n for n in notes if n["feed_id"] not in existing]
    print(f"[2/4] 详情+粉丝补全（待处理 {len(todo)} 条，已跳过 {len(notes)-len(todo)} 条）...")
    cache = {}
    for i, n in enumerate(todo):
        try:
            n = get_feed_detail(n)
            n = get_user_profile(n, cache)
            n["score_info"] = rank_notes([n])[0]["score_info"]
            upsert(n)
            if (i + 1) % 10 == 0 or (i + 1) == len(todo):
                print(f"      → {i+1}/{len(todo)}（库内累计 {con.execute('SELECT COUNT(*) FROM notes').fetchone()[0]} 条）")
        except Exception as e:
            print(f"      → 第 {i+1} 条失败（{n.get('title','')[:16]}）: {e}，继续下一批")
            time.sleep(5)
    con.commit()

    ranked = rank_notes(todo)
    print(f"      → 已完成落库，库内共 {con.execute('SELECT COUNT(*) FROM notes').fetchone()[0]} 条")

    print("\n=== TOP 10 评分结果（本次新增） ===")
    for n in ranked[:10]:
        si = n["score_info"]
        print(f"  [{si['tag']}] {si['score']:5.1f}分  赞{n.get('liked_count')} 粉{n.get('fans_count')}  {n.get('title','')[:24]}")
    con.close()


if __name__ == "__main__":
    main()
