"""共享 fixtures：临时数据库/图片目录（不污染真实 data/）。"""
import datetime
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture()
def now_utc():
    return datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)


@pytest.fixture()
def tmp_db(tmp_path):
    """返回一个初始化了 notes/publish_logs 表的 sqlite 连接（临时文件）。"""
    from codex_xhs.server import _init_tables
    con = sqlite3.connect(tmp_path / "test.db")
    con.row_factory = sqlite3.Row
    _init_tables(con)
    yield con
    con.close()


@pytest.fixture()
def tmp_images(tmp_path, monkeypatch):
    """把 IMAGES_DIR 指向临时目录（清理逻辑测试用）。"""
    from codex_xhs import server
    d = tmp_path / "images"
    d.mkdir()
    monkeypatch.setattr(server, "IMAGES_DIR", d)
    return d
