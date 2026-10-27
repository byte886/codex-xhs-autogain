"""评分引擎单测：覆盖 score_note 全分支 + 全部纯函数。"""
import datetime

import pytest

from codex_xhs import score


def _ts(days_ago: float, base) -> int:
    return int((base - datetime.timedelta(days=days_ago)).timestamp())


class TestToInt:
    def test_plain(self):
        assert score._to_int("1234") == 1234
        assert score._to_int(100) == 100
        assert score._to_int(1.5) == 1
        assert score._to_int(None) == 0
        assert score._to_int("1,200") == 1200

    def test_chinese_units(self):
        assert score._to_int("1.2万") == 12000
        assert score._to_int("3千") == 3000
        assert score._to_int("1.5亿") == 150000000

    def test_garbage(self):
        assert score._to_int("abc") == 0
        assert score._to_int("") == 0


class TestDaysSince:
    def test_recent(self, now_utc):
        assert score._days_since(_ts(10, now_utc), now_utc) == pytest.approx(10.0, abs=0.01)

    def test_missing(self, now_utc):
        assert score._days_since(None, now_utc) == float("inf")
        assert score._days_since(0, now_utc) == float("inf")


class TestRatioScore:
    def test_cap(self):
        s, r = score._ratio_score(1000, 100, 3.0)
        assert s == 40.0
        assert r == 10.0

    def test_proportional(self):
        s, _ = score._ratio_score(100, 100, 3.0)
        assert s == pytest.approx(40.0 / 3)

    def test_no_fans_zero(self):
        s, r = score._ratio_score(0, 0, 3.0)
        assert s == 0.0
        assert r == 0.0

    def test_zero_like_positive_fans(self):
        # liked=0 且 fans>0：else 分支返回 0.0（变异 0->1 应被杀）
        s, r = score._ratio_score(0, 100, 3.0)
        assert s == 0.0 and r == 0.0

    def test_fans_one_boundary(self):
        # fans=1 走除法（fans>0 边界，变异 >->>= 应被杀）
        s, r = score._ratio_score(100, 1, 3.0)
        assert r == 100.0 and s == 40.0

    def test_liked_one_boundary(self):
        # liked=1：liked>0 成立走 else 分支（0->1 于 liked>0 应被杀）
        s, r = score._ratio_score(1, 100, 3.0)
        assert r == pytest.approx(0.01) and s == pytest.approx(40.0 * 0.01 / 3)


class TestLowFanScore:
    def test_zero_fans(self):
        assert score._low_fan_score(0, 500) == 0.0

    def test_full(self):
        assert score._low_fan_score(100, 500) == 25.0

    def test_linear_decay(self):
        assert score._low_fan_score(5000, 500) == 0.0
        assert score._low_fan_score(1000, 500) == pytest.approx(25.0 * (1 - 500 / 4500))

    def test_exact_low_fan_boundary(self):
        # fans == low_fan：两分支都 25.0（<-><= 等价性验证）
        assert score._low_fan_score(500, 500) == 25.0


class TestHealthScore:
    def test_healthy(self):
        assert score._health_score(100, 50, 20, 10) == pytest.approx(20.0 * 80 / 100)

    def test_no_likes(self):
        assert score._health_score(0, 100, 0, 0) == 0.0
        assert score._health_score(100, 0, 0, 0) == 0.0

    def test_zero_like_positive_extra(self):
        # liked=0 且 extra>0：不崩且计 0（liked>0 边界）
        assert score._health_score(0, 5, 0, 0) == 0.0

    def test_like_one(self):
        # liked=1 走计算分支（0->1 于 liked>0 应被杀）
        assert score._health_score(1, 1, 0, 0) == pytest.approx(20.0)


class TestFreshScore:
    def test_full(self):
        assert score._fresh_score(10, 30) == 15.0

    def test_zero(self):
        assert score._fresh_score(90, 30) == 0.0

    def test_linear(self):
        assert score._fresh_score(60, 30) == pytest.approx(15.0 * (1 - 30 / 60))

    def test_exact_fresh_days_boundary(self):
        # days == fresh_days：两分支都 15（<=->< 等价性验证）
        assert score._fresh_score(30, 30) == 15.0


class TestPickTag:
    def test_all_tags(self):
        t = score.DEFAULT_THRESHOLDS
        assert score._pick_tag(100, 0.5, 100, t) == "低赞"
        assert score._pick_tag(600, 4.0, 100, t) == "低粉爆款🔥"
        assert score._pick_tag(400, 0.5, 100, t) == "低粉"
        assert score._pick_tag(600, 0.5, 1000, t) == "高赞"
        assert score._pick_tag(400, 0.5, 1000, t) == "普通"

    def test_boundary_low_like(self):
        # liked == low_like(300)：仍低赞（<=->< 应被杀）
        assert score._pick_tag(300, 0.1, 2000, score.DEFAULT_THRESHOLDS) == "低赞"

    def test_boundary_hot_like_low_fan(self):
        # liked == hot_like(500) 且 ratio 高：不触发低粉爆款（>->>= 应被杀）
        assert score._pick_tag(500, 4.0, 100, score.DEFAULT_THRESHOLDS) == "低粉"

    def test_boundary_low_fan(self):
        # fans == low_fan(500)：不触发低粉（<-><= 应被杀）
        assert score._pick_tag(400, 0.1, 500, score.DEFAULT_THRESHOLDS) == "普通"

    def test_boundary_high_like(self):
        # liked == hot_like(500)：不触发高赞（>->>= 应被杀）
        assert score._pick_tag(500, 0.1, 2000, score.DEFAULT_THRESHOLDS) == "普通"


class TestScoreNote:
    def test_low_like(self, now_utc):
        n = {"liked_count": 100, "fans_count": 1000, "collected_count": 0,
             "comment_count": 0, "shared_count": 0, "publish_time": _ts(1, now_utc)}
        r = score.score_note(n, now=now_utc)
        assert r["tag"] == "低赞"

    def test_low_fan_hit(self, now_utc):
        n = {"liked_count": 1000, "fans_count": 100, "collected_count": 300,
             "comment_count": 50, "shared_count": 10, "publish_time": _ts(1, now_utc)}
        r = score.score_note(n, now=now_utc)
        assert r["tag"] == "低粉爆款🔥"

    def test_low_fan(self, now_utc):
        n = {"liked_count": 400, "fans_count": 100, "collected_count": 0,
             "comment_count": 0, "shared_count": 0, "publish_time": _ts(1, now_utc)}
        assert score.score_note(n, now=now_utc)["tag"] == "低粉"

    def test_high_like(self, now_utc):
        n = {"liked_count": 1000, "fans_count": 2000, "collected_count": 0,
             "comment_count": 0, "shared_count": 0, "publish_time": _ts(1, now_utc)}
        assert score.score_note(n, now=now_utc)["tag"] == "高赞"

    def test_normal(self, now_utc):
        n = {"liked_count": 400, "fans_count": 2000, "collected_count": 0,
             "comment_count": 0, "shared_count": 0, "publish_time": _ts(1, now_utc)}
        assert score.score_note(n, now=now_utc)["tag"] == "普通"

    def test_no_fans_no_time(self, now_utc):
        n = {"liked_count": "600", "fans_count": None, "collected_count": 0,
             "comment_count": 0, "shared_count": 0, "publish_time": None}
        r = score.score_note(n, now=now_utc)
        assert r["tag"] == "低粉爆款🔥"  # 600>500 且 fan_ratio=liked=600>3
        assert r["dimensions"]["freshness"] == 0.0

    def test_string_counts(self, now_utc):
        n = {"liked_count": "1.2万", "fans_count": "3千", "collected_count": "100",
             "comment_count": "5", "shared_count": "0", "publish_time": _ts(1, now_utc)}
        r = score.score_note(n, now=now_utc)
        assert r["fan_ratio"] == pytest.approx(12000 / 3000, abs=0.01)

    def test_score_value_precision(self, now_utc):
        # 精确分数：ratio=4.4 + low_fan=22.2 + health=0 + fresh=15 → 41.7（round 1 位小数）
        n = {"liked_count": 333, "fans_count": 1000, "collected_count": 0,
             "comment_count": 0, "shared_count": 0, "publish_time": _ts(1, now_utc)}
        r = score.score_note(n, now=now_utc)
        assert r["score"] == pytest.approx(41.7, abs=0.1)

    def test_custom_thresholds(self, now_utc):
        n = {"liked_count": 400, "fans_count": 100, "collected_count": 0,
             "comment_count": 0, "shared_count": 0, "publish_time": _ts(1, now_utc)}
        # 默认 low_like=300：400 点赞不低赞（>300），粉丝 100<500 → 低粉
        assert score.score_note(n, now=now_utc)["tag"] == "低粉"
        # 自定义 low_like=1000：400 ≤ 1000 → 低赞（低赞优先级最高）
        t = {**score.DEFAULT_THRESHOLDS, "low_like": 1000}
        assert score.score_note(n, thresholds=t, now=now_utc)["tag"] == "低赞"


class TestRankNotes:
    def test_sorted_and_immutable(self, now_utc):
        low = {"liked_count": 100, "fans_count": 2000, "publish_time": _ts(1, now_utc)}
        high = {"liked_count": 5000, "fans_count": 100, "publish_time": _ts(1, now_utc)}
        ranked = score.rank_notes([low, high], now=now_utc)
        assert ranked[0]["score_info"]["score"] > ranked[1]["score_info"]["score"]
        assert "score_info" not in low
        assert "score_info" not in high
