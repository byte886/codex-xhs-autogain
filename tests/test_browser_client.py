"""浏览器客户端纯逻辑单测：滚动采集终止判定 + URL 编码。"""
from codex_xhs import browser_client as bc


class TestShouldStopCollect:
    def test_count_reached(self):
        assert bc._should_stop_collect(30, 30, 0, 5, None) == (True, 0)

    def test_user_stop(self):
        assert bc._should_stop_collect(5, 30, 0, 1, lambda: True) == (True, 0)

    def test_stale_increments(self):
        stop, stale = bc._should_stop_collect(0, 30, 1, 0, None)
        assert stop is False and stale == 2

    def test_stale_stops_with_results(self):
        assert bc._should_stop_collect(10, 30, 1, 0, None)[0] is True

    def test_stale_resets_on_added(self):
        stop, stale = bc._should_stop_collect(10, 30, 2, 3, None)
        assert stop is False and stale == 0

    def test_no_results_no_stop(self):
        assert bc._should_stop_collect(0, 30, 1, 0, None)[0] is False


class TestUrlEncode:
    def test_quote_space(self):
        assert bc._urlencode("a b") == "a%20b"
