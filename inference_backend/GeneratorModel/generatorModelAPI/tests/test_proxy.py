"""
Integration tests for the /generate_workflow proxy endpoint.

Phase 10.2: the proxy is schema-agnostic. Tests no longer exercise tool
validation (which has moved to the AutoMend core backend). Instead we
assert:
  - Any parsed JSON dict comes through as workflow_spec unchanged.
  - Guardrails repair still runs (markdown fences, trailing commas,
    unclosed brackets).
  - vLLM connection / HTTP errors produce structured failure responses.
  - Request validation rejects missing/empty system_prompt and user_message.

All vLLM HTTP calls are mocked — no GPU, no running vLLM instance needed.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import SAMPLE_SYSTEM_PROMPT, SAMPLE_USER_MESSAGE, make_vllm_response

client = TestClient(app)

ENDPOINT = "/generate_workflow"


def _body(**overrides) -> dict:
    payload = {"system_prompt": SAMPLE_SYSTEM_PROMPT, "user_message": SAMPLE_USER_MESSAGE}
    payload.update(overrides)
    return payload


def _mock_vllm_post(content: str, status_code: int = 200, finish_reason: str = "stop"):
    """Return a mock that simulates httpx.AsyncClient.post → vLLM response."""
    vllm = make_vllm_response(content, status_code, finish_reason)
    mock_response = httpx.Response(
        status_code=vllm["status_code"],
        json=vllm["body"],
        request=httpx.Request("POST", "http://mock:8001/v1/chat/completions"),
    )
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


# ===================================================================
# Passthrough — arbitrary dict shapes come through unchanged
# ===================================================================


class TestPassthrough:
    def test_returns_parsed_dict_unchanged(self):
        spec = {
            "name": "Memory pressure remediation",
            "version": "1.0.0",
            "trigger": {"incident_types": ["incident.memory"]},
            "steps": [
                {"id": "s1", "type": "action", "tool": "scale_deployment",
                 "input": {"namespace": "prod", "workload": "reco", "replicas": 5}},
                {"id": "s2", "type": "notification", "tool": "slack_notification",
                 "input": {"channel": "#mlops"}, "on_success": None},
            ],
        }
        with patch("app.main.httpx.AsyncClient", return_value=_mock_vllm_post(json.dumps(spec))):
            resp = client.post(ENDPOINT, json=_body())

        body = resp.json()
        assert body["success"] is True
        assert body["workflow_spec"] == spec
        assert body["error"] is None

    def test_arbitrary_tool_names_allowed(self):
        """Unknown tool names must NOT be rejected at this layer."""
        spec = {"steps": [{"id": "s1", "tool": "completely_made_up_tool", "input": {"x": 1}}]}
        with patch("app.main.httpx.AsyncClient", return_value=_mock_vllm_post(json.dumps(spec))):
            resp = client.post(ENDPOINT, json=_body())
        assert resp.json()["success"] is True
        assert resp.json()["workflow_spec"]["steps"][0]["tool"] == "completely_made_up_tool"

    def test_legacy_qwen_shape_passes_through(self):
        """The old double-nested {workflow: {workflow: {...}}} shape is just JSON."""
        spec = {"workflow": {"steps": [{"step_id": 1, "tool": "scale_deployment",
                                          "params": {"namespace": "x", "deployment_name": "y", "replicas": 3}}]}}
        with patch("app.main.httpx.AsyncClient", return_value=_mock_vllm_post(json.dumps(spec))):
            resp = client.post(ENDPOINT, json=_body())
        assert resp.json()["workflow_spec"] == spec

    def test_empty_steps_pass_through(self):
        """No per-step validation; empty arrays are fine here."""
        spec = {"steps": []}
        with patch("app.main.httpx.AsyncClient", return_value=_mock_vllm_post(json.dumps(spec))):
            resp = client.post(ENDPOINT, json=_body())
        assert resp.json()["success"] is True
        assert resp.json()["workflow_spec"] == spec

    def test_raw_output_is_returned_on_success(self):
        spec = {"steps": []}
        with patch("app.main.httpx.AsyncClient", return_value=_mock_vllm_post(json.dumps(spec))):
            resp = client.post(ENDPOINT, json=_body())
        assert resp.json()["raw_output"] == json.dumps(spec)


# ===================================================================
# Guardrails repair still works
# ===================================================================


class TestGuardrailsRepair:
    def test_markdown_fences_stripped(self):
        inner = json.dumps({"steps": [{"id": "s1", "type": "action"}]})
        llm_output = f"```json\n{inner}\n```"
        with patch("app.main.httpx.AsyncClient", return_value=_mock_vllm_post(llm_output)):
            resp = client.post(ENDPOINT, json=_body())
        assert resp.json()["success"] is True
        assert resp.json()["workflow_spec"]["steps"][0]["id"] == "s1"

    def test_trailing_comma_repaired(self):
        llm_output = '{"steps": [{"id": "s1", "type": "action",},],}'
        with patch("app.main.httpx.AsyncClient", return_value=_mock_vllm_post(llm_output)):
            resp = client.post(ENDPOINT, json=_body())
        assert resp.json()["success"] is True

    def test_missing_closing_bracket_repaired(self):
        llm_output = '{"steps": [{"id": "s1", "type": "action"}]'
        with patch("app.main.httpx.AsyncClient", return_value=_mock_vllm_post(llm_output, finish_reason="length")):
            resp = client.post(ENDPOINT, json=_body())
        assert resp.json()["success"] is True

    def test_prose_around_json_is_stripped(self):
        llm_output = 'Here is the spec:\n{"steps": [{"id": "s1"}]}\nEnd.'
        with patch("app.main.httpx.AsyncClient", return_value=_mock_vllm_post(llm_output)):
            resp = client.post(ENDPOINT, json=_body())
        assert resp.json()["success"] is True


# ===================================================================
# JSON parsing failures
# ===================================================================


class TestJsonFailures:
    def test_total_garbage_returns_failure(self):
        llm_output = "I cannot help with that."
        with patch("app.main.httpx.AsyncClient", return_value=_mock_vllm_post(llm_output)):
            resp = client.post(ENDPOINT, json=_body())
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "JSON parsing failed"
        assert body["raw_output"] == llm_output

    def test_empty_output(self):
        with patch("app.main.httpx.AsyncClient", return_value=_mock_vllm_post("")):
            resp = client.post(ENDPOINT, json=_body())
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "JSON parsing failed"


# ===================================================================
# vLLM connection failures
# ===================================================================


class TestVllmErrors:
    def test_connection_refused(self):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("app.main.httpx.AsyncClient", return_value=mock_client):
            resp = client.post(ENDPOINT, json=_body())
        body = resp.json()
        assert body["success"] is False
        assert body["error"] == "vLLM connection failed"

    def test_timeout(self):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("app.main.httpx.AsyncClient", return_value=mock_client):
            resp = client.post(ENDPOINT, json=_body())
        assert resp.json()["error"] == "vLLM request timed out"

    def test_vllm_500(self):
        mock_response = httpx.Response(
            status_code=500,
            text="Internal Server Error",
            request=httpx.Request("POST", "http://mock:8001/v1/chat/completions"),
        )
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with patch("app.main.httpx.AsyncClient", return_value=mock_client):
            resp = client.post(ENDPOINT, json=_body())
        assert "500" in resp.json()["error"]


# ===================================================================
# Forwarded prompt content
# ===================================================================


class TestForwardedPrompt:
    def test_system_prompt_forwarded_verbatim(self):
        spec = {"steps": []}
        mock_client = _mock_vllm_post(json.dumps(spec))
        with patch("app.main.httpx.AsyncClient", return_value=mock_client):
            client.post(ENDPOINT, json=_body(system_prompt="CUSTOM SYSTEM PROMPT"))

        sent_body = mock_client.post.call_args.kwargs["json"]
        messages = sent_body["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "CUSTOM SYSTEM PROMPT"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == SAMPLE_USER_MESSAGE

    def test_temperature_defaults_to_zero(self):
        spec = {"steps": []}
        mock_client = _mock_vllm_post(json.dumps(spec))
        with patch("app.main.httpx.AsyncClient", return_value=mock_client):
            client.post(ENDPOINT, json=_body())
        sent = mock_client.post.call_args.kwargs["json"]
        assert sent["temperature"] == 0.0

    def test_max_tokens_respected(self):
        spec = {"steps": []}
        mock_client = _mock_vllm_post(json.dumps(spec))
        with patch("app.main.httpx.AsyncClient", return_value=mock_client):
            client.post(ENDPOINT, json=_body(max_tokens=2048))
        assert mock_client.post.call_args.kwargs["json"]["max_tokens"] == 2048


# ===================================================================
# Request validation
# ===================================================================


class TestRequestValidation:
    def test_missing_system_prompt(self):
        resp = client.post(ENDPOINT, json={"user_message": "x"})
        assert resp.status_code == 422

    def test_missing_user_message(self):
        resp = client.post(ENDPOINT, json={"system_prompt": "x"})
        assert resp.status_code == 422

    def test_empty_system_prompt(self):
        resp = client.post(ENDPOINT, json={"system_prompt": "", "user_message": "x"})
        assert resp.status_code == 422

    def test_empty_user_message(self):
        resp = client.post(ENDPOINT, json={"system_prompt": "x", "user_message": ""})
        assert resp.status_code == 422

    def test_health_endpoint(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    def test_legacy_fields_ignored(self):
        """A pre-10.2 caller sent `system_context`; should not 422 now."""
        spec = {"steps": []}
        with patch("app.main.httpx.AsyncClient", return_value=_mock_vllm_post(json.dumps(spec))):
            resp = client.post(ENDPOINT, json={
                "system_prompt": "s", "user_message": "u",
                "system_context": "this field is gone but should be ignored",
            })
        assert resp.status_code == 200
        assert resp.json()["success"] is True
