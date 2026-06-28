"""
test_llm_router — LLM 设置 + Provider 列表 + Test Connection 端点契约测试。

覆盖：
  GET   /api/settings/llm                读当前设置（api_key 脱敏）
  GET   /api/settings/llm/providers      列出所有 provider
  PUT   /api/settings/llm                切 provider / 改 config / 改默认参数
  POST  /api/settings/llm/test           测试连接（mock OpenAI client）

LLM 真实 HTTP 调用由 monkeypatch 替换 OpenAI client，绕过网络。
"""
from __future__ import annotations
import json


# ============================================================
# GET 当前设置
# ============================================================
def test_get_llm_settings_returns_defaults(client, llm_store):
    """默认初始化后 active_provider=qwen，所有 provider 配置存在。"""
    r = client.get("/api/settings/llm")
    assert r.status_code == 200
    body = r.json()
    assert body["active_provider"] == "qwen"
    assert body["active_provider_name"] == "通义千问 (Qwen)"
    # 6 个 provider 都在
    assert set(body["providers"].keys()) == {"deepseek", "qwen", "zhipu", "ollama", "openai", "agnes"}
    # qwen 默认 base_url / model 来自 PROVIDER_DEFAULTS
    assert body["providers"]["qwen"]["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert body["providers"]["qwen"]["model"] == "qwen-turbo"


def test_get_llm_settings_api_key_is_masked(client, llm_store):
    """写入完整 api_key 后，读取的应该是脱敏串（sk-12****5678 形式）。"""
    store = llm_store
    store.update_provider("qwen", api_key="sk-1234567890abcdef")

    r = client.get("/api/settings/llm")
    body = r.json()
    masked = body["providers"]["qwen"]["api_key"]
    # 不应包含完整明文
    assert "sk-1234567890abcdef" not in masked
    # 应当是 mask_api_key 形式（保留首尾 4 字符）
    assert masked.startswith("sk-1") or "..." in masked


# ============================================================
# GET providers
# ============================================================
def test_list_providers(client):
    r = client.get("/api/settings/llm/providers")
    assert r.status_code == 200
    body = r.json()
    assert len(body["providers"]) == 6
    # 元信息含 needs_key 等
    qwen = next(p for p in body["providers"] if p["id"] == "qwen")
    assert qwen["name"] == "通义千问 (Qwen)"
    assert qwen["needs_key"] == "true"
    # ollama 不需要 key
    ollama = next(p for p in body["providers"] if p["id"] == "ollama")
    assert ollama["needs_key"] == "false"
    # defaults 字段包含每个 provider 的 base_url / model
    assert "qwen" in body["defaults"]
    assert body["defaults"]["qwen"]["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"


# ============================================================
# PUT 更新
# ============================================================
def test_update_active_provider(client, llm_store):
    r = client.put(
        "/api/settings/llm",
        json={"active_provider": "agnes"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["active_provider"] == "agnes"
    # settings 文件确实改了
    assert llm_store.get_active_provider_id() == "agnes"


def test_update_unknown_provider_rejected(client):
    r = client.put(
        "/api/settings/llm",
        json={"active_provider": "fake-not-exist"},
    )
    assert r.status_code == 400
    assert "未知 provider" in r.json()["detail"]


def test_update_active_config_empty_string_does_not_overwrite(
    client, llm_store,
):
    """
    [P0] 关键约束：空串 / None 不覆盖已有值（来自 project_memory 硬约束）。
    先写入完整 base_url + model，再 PUT 一个空串的 active_config → 旧值保留。
    """
    store = llm_store
    store.update_provider(
        "qwen",
        api_key="sk-original",
        base_url="https://original.example.com/v1",
        model="original-model",
    )
    store.set_active_provider("qwen")

    r = client.put(
        "/api/settings/llm",
        json={
            "active_config": {
                "api_key": "",       # 空串 → 不覆盖
                "base_url": "",      # 空串 → 不覆盖
                "model": "",         # 空串 → 不覆盖
            }
        },
    )
    assert r.status_code == 200
    cfg = llm_store.get_provider_with_env_fallback("qwen")
    assert cfg["api_key"] == "sk-original"
    assert cfg["base_url"] == "https://original.example.com/v1"
    assert cfg["model"] == "original-model"


def test_update_active_config_new_value_overwrites(client, llm_store):
    """非空值正常覆盖。"""
    store = llm_store
    store.update_provider("qwen", api_key="sk-old", base_url="https://old/", model="old-model")
    store.set_active_provider("qwen")

    r = client.put(
        "/api/settings/llm",
        json={
            "active_config": {
                "api_key": "sk-new",
                "base_url": "https://new/",
                "model": "new-model",
            }
        },
    )
    assert r.status_code == 200
    cfg = llm_store.get_provider_with_env_fallback("qwen")
    assert cfg["api_key"] == "sk-new"
    assert cfg["base_url"] == "https://new/"
    assert cfg["model"] == "new-model"


def test_update_default_params(client, llm_store):
    r = client.put(
        "/api/settings/llm",
        json={"default_temperature": 0.3, "default_max_tokens": 2048},
    )
    assert r.status_code == 200
    all_data = llm_store.get_all()
    assert all_data["default_temperature"] == 0.3
    assert all_data["default_max_tokens"] == 2048


def test_update_partial_default_params_keeps_others(client, llm_store):
    """只传 temperature → max_tokens 不动。"""
    store = llm_store
    store.update_default_params(temperature=0.5, max_tokens=512)
    r = client.put(
        "/api/settings/llm",
        json={"default_temperature": 0.9},
    )
    assert r.status_code == 200
    all_data = store.get_all()
    assert all_data["default_temperature"] == 0.9
    assert all_data["default_max_tokens"] == 512


# ============================================================
# POST test connection
# ============================================================
def test_test_connection_empty_key_rejected(client, llm_store):
    """非 ollama provider + 空 api_key → success=False + 友好提示。"""
    r = client.post(
        "/api/settings/llm/test",
        json={"provider_id": "qwen", "api_key": ""},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "API Key 为空" in body["message"]
    assert body["provider_id"] == "qwen"


def test_test_connection_ollama_no_key_ok(client, monkeypatch):
    """Ollama 不需要 api_key：空 key 时不应被拒绝。"""
    from openai.types.chat import ChatCompletion, ChatCompletionMessage
    from openai.types.chat.chat_completion import Choice

    class FakeCompletions:
        def create(self, *args, **kwargs):
            return ChatCompletion(
                id="test", model="qwen2.5:7b", object="chat.completion", created=0,
                choices=[Choice(index=0, message=ChatCompletionMessage(role="assistant", content="hi"),
                                finish_reason="stop")],
            )

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.chat = FakeChat()

    # Monkeypatch OpenAI 类
    import backend.api.llm_router as llm_router
    monkeypatch.setattr(llm_router, "OpenAI", FakeClient)

    r = client.post(
        "/api/settings/llm/test",
        json={"provider_id": "ollama", "test_prompt": "ping"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert "连接成功" in body["message"]
    assert body["provider_id"] == "ollama"
    assert body["response_text"] == "hi"


def test_test_connection_unknown_provider_rejected(client):
    r = client.post(
        "/api/settings/llm/test",
        json={"provider_id": "fake-fake", "api_key": "x"},
    )
    assert r.status_code == 400
    assert "未知 provider" in r.json()["detail"]


def test_test_connection_uses_request_overrides(client, monkeypatch):
    """request 显式传的 api_key/base_url/model 应覆盖文件中的配置。"""
    from openai.types.chat import ChatCompletion, ChatCompletionMessage
    from openai.types.chat.chat_completion import Choice

    captured = {}

    class FakeCompletions:
        def create(self, model, messages, **kwargs):
            captured["model"] = model
            return ChatCompletion(
                id="t", model=model, object="chat.completion", created=0,
                choices=[Choice(index=0, message=ChatCompletionMessage(role="assistant", content="ok"),
                                finish_reason="stop")],
            )

    class FakeClient:
        def __init__(self, api_key=None, base_url=None, timeout=None):
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            self.chat = type("C", (), {"completions": FakeCompletions()})()

    import backend.api.llm_router as llm_router
    monkeypatch.setattr(llm_router, "OpenAI", FakeClient)

    r = client.post(
        "/api/settings/llm/test",
        json={
            "provider_id": "qwen",
            "api_key": "sk-test-override",
            "base_url": "https://override.example.com/v1",
            "model": "override-model",
            "test_prompt": "ping",
        },
    )
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert captured["api_key"] == "sk-test-override"
    assert captured["base_url"] == "https://override.example.com/v1"
    assert captured["model"] == "override-model"


def test_test_connection_empty_response_reports_failure(client, monkeypatch):
    """LLM 返回空内容 → success=False + 友好错误。"""
    from openai.types.chat import ChatCompletion, ChatCompletionMessage
    from openai.types.chat.chat_completion import Choice

    class EmptyCompletions:
        def create(self, *args, **kwargs):
            return ChatCompletion(
                id="t", model="m", object="chat.completion", created=0,
                choices=[Choice(index=0, message=ChatCompletionMessage(role="assistant", content=""),
                                finish_reason="stop")],
            )

    class EmptyClient:
        def __init__(self, *a, **k):
            self.chat = type("C", (), {"completions": EmptyCompletions()})()

    import backend.api.llm_router as llm_router
    monkeypatch.setattr(llm_router, "OpenAI", EmptyClient)

    r = client.post(
        "/api/settings/llm/test",
        json={"provider_id": "qwen", "api_key": "sk-x", "test_prompt": "ping"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "空内容" in body["message"] or "权限" in body["message"]


def test_test_connection_exception_returns_failure(client, monkeypatch):
    """OpenAI client 抛异常 → success=False + 错误信息。"""
    class BoomClient:
        def __init__(self, *a, **k):
            pass

        @property
        def chat(self):
            raise RuntimeError("连接超时")

    import backend.api.llm_router as llm_router
    monkeypatch.setattr(llm_router, "OpenAI", BoomClient)

    r = client.post(
        "/api/settings/llm/test",
        json={"provider_id": "qwen", "api_key": "sk-x"},
    )
    # 端点会把异常 catch 住 → success=False（HTTP 仍 200）
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert "错误" in body["message"] or "超时" in body["message"]
