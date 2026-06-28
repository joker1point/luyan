"""
LLM API Tester 单元测试

测试目标：
  1. _is_anthropic_base — Anthropic 域名探测
  2. _join_url — URL 拼接去重
  3. _build_headers — 认证头构造（Anthropic vs OpenAI）
  4. _resolve_config — 配置解析
  5. fetch_models — 模型列表拉取（mocked）
  6. test_stream_latency — 流式延迟测试（mocked）
  7. probe_request — 原始请求探针（mocked）
  8. _parse_sse_line — SSE 流式解析
  9. _build_chat_body / _safe_message — 辅助函数

预期运行方式：python -m pytest tests/test_llm_api_tester.py -v
使用 unittest.mock 模拟所有 HTTP 调用。
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from requests import Response

from backend.services import llm_api_tester
from backend.services.llm_api_tester import (
    _is_anthropic_base,
    _join_url,
    _build_headers,
    _resolve_config,
    _parse_sse_line,
    _safe_message,
    _build_chat_body,
    fetch_models,
    test_stream_latency,
    probe_request,
)


# ==============================================================================
# Test Suite 1：域名探测
# ==============================================================================

class TestIsAnthropicBase:
    def test_anthropic_domain(self):
        assert _is_anthropic_base("https://api.anthropic.com") is True
        assert _is_anthropic_base("https://api.anthropic.com/v1") is True

    def test_openai_domain(self):
        assert _is_anthropic_base("https://api.openai.com") is False

    def test_empty_base(self):
        assert _is_anthropic_base("") is False

    def test_invalid_url(self):
        assert _is_anthropic_base("not-a-url") is False


# ==============================================================================
# Test Suite 2：URL 拼接去重
# ==============================================================================

class TestJoinUrl:
    def test_normal_join(self):
        assert _join_url("https://api.openai.com", "/v1/models") == "https://api.openai.com/v1/models"

    def test_with_v1_base(self):
        """base_url 已带 /v1，suffix 又以 /v1 开头时不应重复"""
        assert _join_url("https://apihub.agnes-ai.com/v1", "/v1/models") == "https://apihub.agnes-ai.com/v1/models"
        assert _join_url("https://api.openai.com/v1/", "/v1/chat/completions") == "https://api.openai.com/v1/chat/completions"

    def test_empty_suffix(self):
        assert _join_url("https://api.openai.com", "") == "https://api.openai.com"

    def test_trailing_slash_base(self):
        assert _join_url("https://api.openai.com/v1/", "/v1/models") == "https://api.openai.com/v1/models"


# ==============================================================================
# Test Suite 3：请求头构造
# ==============================================================================

class TestBuildHeaders:
    def test_openai_bearer(self):
        headers = _build_headers("sk-test", "https://api.openai.com")
        assert headers["Authorization"] == "Bearer sk-test"
        assert headers["Content-Type"] == "application/json"

    def test_anthropic_x_api_key(self):
        headers = _build_headers("sk-ant-test", "https://api.anthropic.com")
        assert headers["x-api-key"] == "sk-ant-test"
        assert headers["anthropic-version"] == "2023-06-01"
        assert "Authorization" not in headers

    def test_anthropic_subdomain(self):
        """anthropic.com 的子域名也属于 Anthropic"""
        headers = _build_headers("sk-ant-test", "https://api.anthropic.com/v1")
        assert headers["x-api-key"] == "sk-ant-test"


# ==============================================================================
# Test Suite 4：配置解析
# ==============================================================================

class TestResolveConfig:
    """注意：_resolve_config 内部会创建 LLMSettingsStore，需要临时路径"""

    @pytest.fixture(autouse=True)
    def _patch_store_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr("backend.services.llm_settings_store._SETTINGS_DIR", str(tmp_path))
        monkeypatch.setattr("backend.services.llm_settings_store._SETTINGS_FILE", str(tmp_path / "llm_settings.json"))

    def test_resolve_defaults(self):
        pid, cfg = _resolve_config()
        assert pid
        assert "api_key" in cfg
        assert "base_url" in cfg
        assert "model" in cfg


# ==============================================================================
# Test Suite 5：辅助函数
# ==============================================================================

class TestSafeMessage:
    def test_normal_message(self):
        assert _safe_message("Hello") == "Hello"

    def test_none_message(self):
        assert _safe_message(None) == "Hi"

    def test_truncated_message(self):
        long = "x" * 5000
        result = _safe_message(long)
        assert len(result) <= 2000

    def test_empty_message(self):
        assert _safe_message("") == "Hi"


class TestBuildChatBody:
    def test_openai_format(self):
        body = _build_chat_body("gpt-4", "Hi", 16, is_anthropic=False)
        assert body["model"] == "gpt-4"
        assert body["stream"] is True
        assert body["messages"][0]["role"] == "user"

    def test_anthropic_format(self):
        body = _build_chat_body("claude-sonnet-4-20250514", "Hi", 16, is_anthropic=True)
        assert body["model"] == "claude-sonnet-4-20250514"
        assert body["stream"] is True
        assert body["max_tokens"] == 16

    def test_max_tokens_clamped(self):
        body = _build_chat_body("gpt-4", "Hi", 99999, is_anthropic=False)
        assert body["max_tokens"] <= 2048


# ==============================================================================
# Test Suite 6：SSE 行解析
# ==============================================================================

class TestParseSseLine:
    def test_skip_non_data(self):
        buf, text, finished = _parse_sse_line(":comment\n", is_anthropic=False)
        assert text == ""
        assert finished is False

    def test_openai_content(self):
        data = json.dumps({"choices": [{"delta": {"content": "你好"}, "finish_reason": None}]})
        buf, text, finished = _parse_sse_line(f"data: {data}\n", is_anthropic=False)
        assert text == "你好"
        assert finished is False

    def test_openai_finish_reason(self):
        data = json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        buf, text, finished = _parse_sse_line(f"data: {data}\n", is_anthropic=False)
        assert text == ""
        assert finished is True

    def test_anthropic_content_block_delta(self):
        data = json.dumps({"type": "content_block_delta", "delta": {"text": "Hello"}})
        buf, text, finished = _parse_sse_line(f"data: {data}\n", is_anthropic=True)
        assert text == "Hello"

    def test_anthropic_message_stop(self):
        data = json.dumps({"type": "message_stop"})
        buf, text, finished = _parse_sse_line(f"data: {data}\n", is_anthropic=True)
        assert finished is True

    def test_done_signal(self):
        buf, text, finished = _parse_sse_line("data: [DONE]\n", is_anthropic=False)
        assert finished is True

    def test_empty_payload_after_prefix(self):
        buf, text, finished = _parse_sse_line("data: \n", is_anthropic=False)
        assert finished is True

    def test_malformed_json_skipped(self):
        data = "data: {这不是JSON}\n"
        buf, text, finished = _parse_sse_line(data, is_anthropic=False)
        assert text == ""
        assert finished is False

    def test_incomplete_line(self):
        """没有换行符的行应留在 buffer 中"""
        buf, text, finished = _parse_sse_line("data: hi", is_anthropic=False)
        assert buf == "data: hi"
        assert text == ""
        assert finished is False


# ==============================================================================
# Test Suite 7：fetch_models（mocked）
# ==============================================================================

class TestFetchModels:
    def _mock_response(self, status=200, json_data=None):
        resp = MagicMock(spec=Response)
        resp.ok = status == 200
        resp.status_code = status
        resp.text = ""
        resp.reason = "OK" if status == 200 else "Error"
        resp.json.return_value = json_data or {"data": []}
        return resp

    @patch("backend.services.llm_api_tester.requests.get")
    def test_fetch_models_success(self, mock_get, monkeypatch, tmp_path):
        monkeypatch.setattr("backend.services.llm_settings_store._SETTINGS_DIR", str(tmp_path))
        monkeypatch.setattr("backend.services.llm_settings_store._SETTINGS_FILE", str(tmp_path / "llm_settings.json"))

        mock_get.return_value = self._mock_response(200, {
            "data": [
                {"id": "gpt-4", "owned_by": "openai", "object": "model"},
                {"id": "gpt-4o-mini", "owned_by": "openai", "object": "model"},
            ]
        })

        result = fetch_models(provider_id="openai", override_api_key="sk-test", override_base_url="https://api.openai.com")

        assert result["raw_count"] == 2
        assert result["models"][0]["id"] == "gpt-4"
        assert result["provider_id"] == "openai"
        assert result["duration_ms"] >= 0

    @patch("backend.services.llm_api_tester.requests.get")
    def test_fetch_models_http_error(self, mock_get, monkeypatch, tmp_path):
        monkeypatch.setattr("backend.services.llm_settings_store._SETTINGS_DIR", str(tmp_path))
        monkeypatch.setattr("backend.services.llm_settings_store._SETTINGS_FILE", str(tmp_path / "llm_settings.json"))

        resp = self._mock_response(401)
        resp.reason = "Unauthorized"
        resp.text = '{"error": "invalid_api_key"}'
        mock_get.return_value = resp

        with pytest.raises(RuntimeError, match="HTTP 401"):
            fetch_models(provider_id="openai", override_api_key="sk-bad", override_base_url="https://api.openai.com")

    def test_fetch_models_empty_base_url(self, monkeypatch, tmp_path):
        monkeypatch.setattr("backend.services.llm_settings_store._SETTINGS_DIR", str(tmp_path))
        monkeypatch.setattr("backend.services.llm_settings_store._SETTINGS_FILE", str(tmp_path / "llm_settings.json"))

        with pytest.raises(ValueError, match="base_url 为空"):
            fetch_models(provider_id="openai", override_api_key="sk-test", override_base_url="")


# ==============================================================================
# Test Suite 8：test_stream_latency（mocked）
# ==============================================================================

class TestStreamLatency:
    def _make_stream_response(self, chunks):
        """生成模拟的流式响应行列表"""
        lines = []
        for chunk in chunks:
            line = f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            lines.append(line.encode("utf-8"))
        return lines

    @patch("backend.services.llm_api_tester.requests.post")
    def test_stream_latency_success(self, mock_post, monkeypatch, tmp_path):
        monkeypatch.setattr("backend.services.llm_settings_store._SETTINGS_DIR", str(tmp_path))
        monkeypatch.setattr("backend.services.llm_settings_store._SETTINGS_FILE", str(tmp_path / "llm_settings.json"))

        resp = MagicMock(spec=Response)
        resp.ok = True
        resp.status_code = 200
        resp.text = ""
        resp.reason = "OK"
        resp.iter_lines.return_value = iter([
            b"data: {\"choices\": [{\"delta\": {\"content\": \"你好\"}, \"finish_reason\": null}]}\n\n",
            b"data: {\"choices\": [{\"delta\": {\"content\": \"世界\"}, \"finish_reason\": null}]}\n\n",
            b"data: {\"choices\": [{\"delta\": {}, \"finish_reason\": \"stop\"}]}\n\n",
        ])
        mock_post.return_value = resp

        result = test_stream_latency(
            provider_id="openai", override_api_key="sk-test",
            override_base_url="https://api.openai.com", override_model="gpt-4o-mini",
            test_message="Hi", max_tokens=16,
        )

        assert result["status"] == 200
        assert result["content"] == "你好世界"
        assert result["ttft_ms"] is not None
        assert result["total_ms"] is not None
        assert result["error"] is None

    @patch("backend.services.llm_api_tester.requests.post")
    def test_stream_latency_http_error(self, mock_post, monkeypatch, tmp_path):
        monkeypatch.setattr("backend.services.llm_settings_store._SETTINGS_DIR", str(tmp_path))
        monkeypatch.setattr("backend.services.llm_settings_store._SETTINGS_FILE", str(tmp_path / "llm_settings.json"))

        resp = MagicMock(spec=Response)
        resp.ok = False
        resp.status_code = 401
        resp.reason = "Unauthorized"
        resp.text = '{"error": "auth failed"}'
        mock_post.return_value = resp

        result = test_stream_latency(
            provider_id="openai", override_api_key="sk-bad",
            override_base_url="https://api.openai.com",
        )
        assert result["status"] == 401
        assert "HTTP 401" in (result.get("error") or "")

    def test_stream_latency_empty_base_url(self):
        result = test_stream_latency(
            provider_id="openai", override_base_url="",
        )
        assert result["error"] == "base_url 为空"


# ==============================================================================
# Test Suite 9：probe_request（mocked）
# ==============================================================================

class TestProbeRequest:
    @patch("backend.services.llm_api_tester.requests.post")
    def test_probe_success(self, mock_post, monkeypatch, tmp_path):
        monkeypatch.setattr("backend.services.llm_settings_store._SETTINGS_DIR", str(tmp_path))
        monkeypatch.setattr("backend.services.llm_settings_store._SETTINGS_FILE", str(tmp_path / "llm_settings.json"))

        resp = MagicMock(spec=Response)
        resp.ok = True
        resp.status_code = 200
        resp.reason = "OK"
        resp.text = '{"choices": [{"message": {"content": "Hello"}}]}'
        resp.json.return_value = {"choices": [{"message": {"content": "Hello"}}]}
        resp.headers = {"content-type": "application/json"}
        mock_post.return_value = resp

        result = probe_request(
            provider_id="openai", override_api_key="sk-test",
            override_base_url="https://api.openai.com",
        )

        assert result["error"] is None
        assert result["response"]["status"] == 200
        assert "request" in result
        # 请求头中的 Authorization 应脱敏
        assert result["request"]["headers"]["Authorization"] == "***"
        # body 中的 stream 应为 False（探针强制非流式）
        assert result["request"]["body"]["stream"] is False

    @patch("backend.services.llm_api_tester.requests.post")
    def test_probe_connection_error(self, mock_post, monkeypatch, tmp_path):
        monkeypatch.setattr("backend.services.llm_settings_store._SETTINGS_DIR", str(tmp_path))
        monkeypatch.setattr("backend.services.llm_settings_store._SETTINGS_FILE", str(tmp_path / "llm_settings.json"))

        from requests.exceptions import ConnectionError
        mock_post.side_effect = ConnectionError("无法连接到服务器")

        result = probe_request(
            provider_id="openai", override_api_key="sk-test",
            override_base_url="https://api.openai.com",
        )
        assert "无法连接" in (result.get("error") or "")
