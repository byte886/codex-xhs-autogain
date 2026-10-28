"""O1 LLM 配置模块单测：掩码、保存回退、门控、路由接线。"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from codex_xhs import llm_service, server


@pytest.fixture()
def llm_cfg(tmp_path, monkeypatch):
    """把 llm_config.json 指向临时目录，测试间互不污染。"""
    monkeypatch.setattr(llm_service, "CONFIG_PATH", tmp_path / "llm_config.json")
    return tmp_path


def test_public_config_unconfigured(llm_cfg):
    cfg = llm_service.public_config()
    assert cfg["configured"] is False
    assert cfg["llm_api_key_masked"] == ""


def test_save_and_mask(llm_cfg):
    llm_service.save_config("sk-abcdef1234567890", "https://api.openai.com/v1", "gpt-4o-mini")
    cfg = llm_service.public_config()
    assert cfg["configured"] is True
    masked = cfg["llm_api_key_masked"]
    assert masked.startswith("sk-a") and masked.endswith("7890")
    assert "abcdef123456" not in masked
    # 明文只存文件，不返前端
    raw = llm_service._load()  # noqa: SLF001
    assert raw["llm_api_key"] == "sk-abcdef1234567890"


def test_save_keeps_old_key_when_empty(llm_cfg):
    llm_service.save_config("sk-key12345678", "https://api.openai.com/v1", "gpt-4o-mini")
    llm_service.save_config("", "https://api.deepseek.com/v1", "deepseek-chat")
    raw = llm_service._load()  # noqa: SLF001
    assert raw["llm_api_key"] == "sk-key12345678"
    assert raw["openai_base_url"] == "https://api.deepseek.com/v1"


def test_require_llm_gate(llm_cfg):
    with pytest.raises(ValueError, match="未配置 AI 模型"):
        llm_service.require_llm()
    llm_service.save_config("sk-k", "https://api.openai.com/v1", "gpt-4o-mini")
    assert llm_service.require_llm()["default_model"] == "gpt-4o-mini"


def test_enabled_flag(llm_cfg):
    assert llm_service.enabled() is False
    llm_service.save_config("sk-k", "https://api.openai.com/v1", "gpt-4o-mini")
    assert llm_service.enabled() is True


def test_test_with_saved_fallback(llm_cfg, monkeypatch):
    """表单 key 为空 → 回退已存配置发起测试。"""
    llm_service.save_config("sk-saved", "https://api.openai.com/v1", "gpt-4o-mini")
    called = {}

    async def fake_get(self, url, **kw):
        called["url"] = url
        assert kw["headers"]["Authorization"] == "Bearer sk-saved"
        return _FakeResp(200, {"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}]})

    monkeypatch.setattr(llm_service.httpx.AsyncClient, "get", fake_get)
    r = asyncio.run(llm_service.test_with_saved("", "", ""))
    assert r["ok"] is True and "gpt-4o-mini" in r["models"]
    assert called["url"].endswith("/models")


def test_test_with_saved_incomplete(llm_cfg):
    r = asyncio.run(llm_service.test_with_saved("", "", ""))
    assert r["ok"] is False and "未填写完整" in r["error"]


# ---- 路由接线 ----
def test_routes_llm_config(llm_cfg, monkeypatch):
    monkeypatch.setattr(server, "llm_service", llm_service)
    c = TestClient(server.app)
    r = c.get("/api/llm_config")
    assert r.status_code == 200 and r.json()["configured"] is False

    r = c.post("/api/llm_config", json={"api_key": "sk-abc", "openai_base_url": "https://api.openai.com/v1", "default_model": "gpt-4o-mini"})
    assert r.status_code == 200 and r.json()["configured"] is True

    r = c.get("/api/llm_config")
    assert "sk-abc" not in r.json()["llm_api_key_masked"]


def test_routes_llm_test_unconfigured(llm_cfg, monkeypatch):
    monkeypatch.setattr(server, "llm_service", llm_service)
    c = TestClient(server.app)
    r = c.post("/api/llm_test", json={"api_key": "", "openai_base_url": "", "default_model": ""})
    assert r.status_code == 200
    assert r.json()["ok"] is False


# ---- O1-2 AI 文稿生成路由 ----
def test_ai_generate_unknown_action(llm_cfg, monkeypatch):
    monkeypatch.setattr(server, "llm_service", llm_service)
    c = TestClient(server.app)
    r = c.post("/api/ai_generate", json={"action": "nope", "note": {}})
    assert r.status_code == 400 and "未知 AI 动作" in r.json()["detail"]


def test_ai_generate_gate_when_unconfigured(llm_cfg, monkeypatch):
    """未配置 LLM → 400（Key 门控）。"""
    monkeypatch.setattr(server, "llm_service", llm_service)
    c = TestClient(server.app)
    r = c.post("/api/ai_generate", json={"action": "title_rewrite", "note": {"title": "翡翠怎么挑"}})
    assert r.status_code == 400 and "未配置 AI 模型" in r.json()["detail"]


def test_ai_generate_success(llm_cfg, monkeypatch):
    """已配置 → mock chat 返回结果。"""
    llm_service.save_config("sk-k", "https://api.openai.com/v1", "gpt-4o-mini")
    monkeypatch.setattr(server, "llm_service", llm_service)

    async def fake_chat(system, user, **kw):
        assert "标题" in user
        return "翡翠小白避坑指南"

    monkeypatch.setattr(llm_service, "chat", fake_chat)
    c = TestClient(server.app)
    r = c.post("/api/ai_generate", json={"action": "title_rewrite", "note": {"title": "翡翠怎么挑"}})
    assert r.status_code == 200
    assert r.json()["ok"] is True and r.json()["result"] == "翡翠小白避坑指南"


# ---- O1-7 AI 文稿质量保障：人设注入 + 爆文模板 ----
def test_ai_generate_persona_injected(llm_cfg, monkeypatch, tmp_path):
    """已配置人设 → system prompt 注入行业/口吻/主题。"""
    llm_service.save_config("sk-k", "https://api.openai.com/v1", "gpt-4o-mini")
    monkeypatch.setattr(server, "llm_service", llm_service)
    monkeypatch.setattr(server, "PERSONA_PATH", tmp_path / "persona.json")
    server._save_persona({"industry": "珠宝翡翠", "tone": "干货型", "topics": "避坑指南、性价比测评"})

    captured = {}

    async def fake_chat(system, user, **kw):
        captured["system"] = system
        return "ok"

    monkeypatch.setattr(llm_service, "chat", fake_chat)
    c = TestClient(server.app)
    r = c.post("/api/ai_generate", json={"action": "body_create", "note": {"title": "翡翠怎么挑", "desc": "讲透种水"}})
    assert r.status_code == 200
    assert "行业【珠宝翡翠】" in captured["system"]
    assert "口吻【干货型】" in captured["system"]
    assert "常发主题【避坑指南、性价比测评】" in captured["system"]


def test_ai_generate_persona_empty_keeps_default(llm_cfg, monkeypatch, tmp_path):
    """未配置人设 → system prompt 保持通用助手（行为不变）。"""
    llm_service.save_config("sk-k", "https://api.openai.com/v1", "gpt-4o-mini")
    monkeypatch.setattr(server, "llm_service", llm_service)
    monkeypatch.setattr(server, "PERSONA_PATH", tmp_path / "persona.json")

    captured = {}

    async def fake_chat(system, user, **kw):
        captured["system"] = system
        return "ok"

    monkeypatch.setattr(llm_service, "chat", fake_chat)
    c = TestClient(server.app)
    r = c.post("/api/ai_generate", json={"action": "title_rewrite", "note": {"title": "翡翠怎么挑"}})
    assert r.status_code == 200
    assert "账号人设" not in captured["system"]
    assert "小红书内容创作助手" in captured["system"]


def test_ai_generate_body_prompt_has_golden_structure(llm_cfg, monkeypatch):
    """body_create 动作提示内置爆文黄金结构（质量保障模板）。"""
    llm_service.save_config("sk-k", "https://api.openai.com/v1", "gpt-4o-mini")
    monkeypatch.setattr(server, "llm_service", llm_service)

    captured = {}

    async def fake_chat(system, user, **kw):
        captured["user"] = user
        return "ok"

    monkeypatch.setattr(llm_service, "chat", fake_chat)
    c = TestClient(server.app)
    r = c.post("/api/ai_generate", json={"action": "body_create", "note": {"title": "翡翠", "desc": "种水干货"}})
    assert r.status_code == 200
    assert "黄金结构" in captured["user"]
    assert "痛点/场景开头" in captured["user"]
    assert "价值收尾" in captured["user"]


class _FakeResp:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json = json_data
        self.text = "fake"

    def json(self):
        return self._json
