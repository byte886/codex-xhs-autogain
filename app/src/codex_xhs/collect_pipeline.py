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


def get_feed_detail(note: dict) -> dict:
    """补全发布时间与完整互动数据。

    get_feed_detail 返回结构：{ data: { note: {...} } }，
    note.time 为毫秒级时间戳（如 1778155374000）→ 转秒存库。
    """
    res = mcp_call("get_feed_detail", {
        "feed_id": note["feed_id"],
        "xsec_token": note["xsec_token"],
    })
    detail = res.get("data", {}).get("note", {}) or res.get("note", {})
    if isinstance(detail, dict):
        inter = detail.get("interactInfo", {})
        ts = detail.get("time")
        # time 为毫秒时间戳；兼容 int/float/字符串，解析失败置 None（不阻断落库）
        if isinstance(ts, (int, float)):
            note["publish_time"] = int(ts / 1000)
        elif isinstance(ts, str) and ts.strip().isdigit():
            note["publish_time"] = int(int(ts) / 1000)
        else:
            note["publish_time"] = None
        note["liked_count"] = inter.get("likedCount", note.get("liked_count", "0"))
        note["collected_count"] = inter.get("collectedCount", note.get("collected_count", "0"))
        note["comment_count"] = inter.get("commentCount", note.get("comment_count", "0"))
        note["shared_count"] = inter.get("sharedCount", note.get("shared_count", "0"))
        note["desc"] = detail.get("desc", "")
        note["ip_location"] = detail.get("ipLocation", "")
    time.sleep(REQUEST_INTERVAL[0] + random.random() * (REQUEST_INTERVAL[1] - REQUEST_INTERVAL[0]))
    return note


def get_user_profile(note: dict, cache: dict) -> dict:
    """拿作者粉丝数（按 user_id 缓存）。"""
    uid = note.get("user_id")
    if not uid:
        return note
    if uid in cache:
        note["fans_count"] = cache[uid]
        return note
    res = mcp_call("user_profile", {
        "user_id": uid,
        "xsec_token": note.get("xsec_token", ""),
    })
    fans = 0
    interactions = res.get("interactions", [])
    if isinstance(interactions, list):
        for it in interactions:
            if it.get("type") == "fans":
                try:
                    fans = int(it.get("count", 0))
                except (TypeError, ValueError):
                    fans = 0
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
            res = mcp_call("search_feeds", {"keyword": kw, "filters": {"sort_by": "综合"}})
            for n in parse_feeds(res):
                if n["feed_id"] not in seen:
                    seen.add(n["feed_id"])
                    n["keyword"] = kw
                    all_notes.append(n)
            print(f"      「{kw}」→ +{len(parse_feeds(res))} 条，累计去重 {len(all_notes)} 条")
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
