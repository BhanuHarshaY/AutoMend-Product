"""Tests for ArchitectClient (§16).

Uses httpx mock transport — no real API calls.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.architect_client import ArchitectClient, _extract_json


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_TOOLS = [
    {
        "name": "fetch_pod_logs",
        "description": "Fetch logs from a pod",
        "side_effect_level": "read",
        "input_schema": {"type": "object", "properties": {"namespace": {"type": "string"}}},
        "required_approvals": 0,
    },
    {
        "name": "restart_workload",
        "description": "Restart a workload",
        "side_effect_level": "write",
        "input_schema": {"type": "object"},
        "required_approvals": 1,
    },
]

SAMPLE_WORKFLOW_SPEC = {
    "name": "GPU Recovery",
    "description": "Handles GPU OOM",
    "version": "1.0.0",
    "trigger": {"incident_types": ["incident.gpu_memory_failure"]},
    "steps": [
        {"id": "fetch", "name": "Fetch Logs", "type": "action", "tool": "fetch_pod_logs",
         "input": {"namespace": "${incident.entity.namespace}"}},
        {"id": "restart", "name": "Restart", "type": "action", "tool": "restart_workload",
         "input": {"namespace": "ml", "workload_type": "deployment", "workload_name": "trainer"}},
    ],
}


def _mock_anthropic_response(spec: dict) -> dict:
    """Build a mock Anthropic Messages API response."""
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "text", "text": json.dumps(spec)},
        ],
        "model": "claude-sonnet-4-20250514",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 100, "output_tokens": 200},
    }


# ---------------------------------------------------------------------------
# JSON extraction unit tests
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_clean_json(self):
        result = _extract_json('{"name": "test"}')
        assert result == {"name": "test"}

    def test_json_with_code_fence(self):
        text = '```json\n{"name": "test"}\n```'
        result = _extract_json(text)
        assert result == {"name": "test"}

    def test_json_with_plain_code_fence(self):
        text = '```\n{"name": "test"}\n```'
        result = _extract_json(text)
        assert result == {"name": "test"}

    def test_json_with_whitespace(self):
        text = '  \n  {"name": "test"}  \n  '
        result = _extract_json(text)
        assert result == {"name": "test"}

    def test_complex_spec(self):
        result = _extract_json(json.dumps(SAMPLE_WORKFLOW_SPEC))
        assert result["name"] == "GPU Recovery"
        assert len(result["steps"]) == 2

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json("not json at all")


# ---------------------------------------------------------------------------
# Prompt building tests
# ---------------------------------------------------------------------------


class TestPromptBuilding:
    def test_system_prompt_contains_tools(self):
        client = ArchitectClient(api_key="test")
        prompt = client._build_system_prompt(SAMPLE_TOOLS)
        assert "fetch_pod_logs" in prompt
        assert "restart_workload" in prompt
        assert "Side effect level: read" in prompt
        assert "Side effect level: write" in prompt
        assert "Required approvals: 1" in prompt

    def test_system_prompt_contains_dsl_schema(self):
        client = ArchitectClient(api_key="test")
        prompt = client._build_system_prompt([])
        assert "Playbook DSL Schema" in prompt
        assert "incident_types" in prompt
        assert "on_failure" in prompt

    def test_system_prompt_with_examples(self):
        client = ArchitectClient(api_key="test")
        examples = [{"name": "Example PB", "workflow_spec": SAMPLE_WORKFLOW_SPEC}]
        prompt = client._build_system_prompt(SAMPLE_TOOLS, example_playbooks=examples)
        assert "Example Playbooks" in prompt
        assert "Example PB" in prompt

    def test_system_prompt_with_policies(self):
        client = ArchitectClient(api_key="test")
        policies = ["Always require approval for destructive actions", "Max 10 steps"]
        prompt = client._build_system_prompt(SAMPLE_TOOLS, policies=policies)
        assert "Policies" in prompt
        assert "Always require approval" in prompt

    def test_system_prompt_without_examples_or_policies(self):
        client = ArchitectClient(api_key="test")
        prompt = client._build_system_prompt(SAMPLE_TOOLS)
        assert "Example Playbooks" not in prompt
        assert "Policies" not in prompt

    def test_user_prompt_basic(self):
        client = ArchitectClient(api_key="test")
        prompt = client._build_user_prompt("restart a crashed pod")
        assert "restart a crashed pod" in prompt

    def test_user_prompt_with_incident_types(self):
        client = ArchitectClient(api_key="test")
        prompt = client._build_user_prompt(
            "handle GPU OOM",
            target_incident_types=["incident.gpu_memory_failure"],
        )
        assert "incident.gpu_memory_failure" in prompt


# ---------------------------------------------------------------------------
# API call tests (mocked httpx)
# ---------------------------------------------------------------------------


class TestGenerateWorkflow:
    async def test_calls_anthropic_api(self):
        """Verify the API call structure and JSON extraction."""
        captured_request = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured_request["url"] = str(request.url)
            captured_request["headers"] = dict(request.headers)
            captured_request["body"] = json.loads(request.content)
            return httpx.Response(200, json=_mock_anthropic_response(SAMPLE_WORKFLOW_SPEC))

        transport = httpx.MockTransport(handler)
        client = ArchitectClient(api_key="sk-test", base_url="http://mock", model="claude-test")

        original_init = httpx.AsyncClient.__init__

        def patched_init(self_client, **kwargs):
            kwargs.pop("timeout", None)
            original_init(self_client, transport=transport, timeout=120, **kwargs)

        httpx.AsyncClient.__init__ = patched_init
        try:
            result = await client.generate_workflow(
                intent="restart crashed GPU workload",
                tools=SAMPLE_TOOLS,
                target_incident_types=["incident.gpu_memory_failure"],
            )
        finally:
            httpx.AsyncClient.__init__ = original_init

        # Verify API call
        assert "http://mock/v1/messages" in captured_request["url"]
        assert captured_request["headers"]["x-api-key"] == "sk-test"
        assert captured_request["body"]["model"] == "claude-test"
        assert captured_request["body"]["max_tokens"] == 4096
        assert len(captured_request["body"]["messages"]) == 1
        assert "restart crashed GPU" in captured_request["body"]["messages"][0]["content"]

        # Verify system prompt contains tools
        system = captured_request["body"]["system"]
        assert "fetch_pod_logs" in system
        assert "restart_workload" in system

        # Verify parsed result
        assert result["name"] == "GPU Recovery"
        assert len(result["steps"]) == 2

    async def test_handles_code_fenced_response(self):
        """When the LLM wraps JSON in code fences, it's still parsed."""
        fenced_response = {
            "content": [
                {"type": "text", "text": f"```json\n{json.dumps(SAMPLE_WORKFLOW_SPEC)}\n```"},
            ],
        }

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=fenced_response)

        transport = httpx.MockTransport(handler)
        client = ArchitectClient(api_key="sk-test", base_url="http://mock")

        original_init = httpx.AsyncClient.__init__

        def patched_init(self_client, **kwargs):
            kwargs.pop("timeout", None)
            original_init(self_client, transport=transport, timeout=120, **kwargs)

        httpx.AsyncClient.__init__ = patched_init
        try:
            result = await client.generate_workflow("test", tools=SAMPLE_TOOLS)
        finally:
            httpx.AsyncClient.__init__ = original_init

        assert result["name"] == "GPU Recovery"


# ---------------------------------------------------------------------------
# Init tests
# ---------------------------------------------------------------------------


class TestClientInit:
    def test_defaults_from_settings(self):
        client = ArchitectClient()
        assert "anthropic" in client.base_url
        assert client.model == "claude-sonnet-4-20250514"
        assert client.provider == "anthropic"
        assert client.local_endpoint == "/generate_workflow"

    def test_custom_overrides(self):
        client = ArchitectClient(api_key="k", base_url="http://x", model="m")
        assert client.api_key == "k"
        assert client.base_url == "http://x"
        assert client.model == "m"

    def test_provider_override(self):
        client = ArchitectClient(provider="local", base_url="http://proxy:8002")
        assert client.provider == "local"
        assert client.base_url == "http://proxy:8002"

    def test_provider_case_insensitive(self):
        client = ArchitectClient(provider="LOCAL")
        assert client.provider == "local"

    def test_local_endpoint_override(self):
        client = ArchitectClient(provider="local", local_endpoint="/v2/generate")
        assert client.local_endpoint == "/v2/generate"


# ---------------------------------------------------------------------------
# Local-provider API call tests
# ---------------------------------------------------------------------------


def _mock_proxy_success(spec: dict, raw_output: str | None = None) -> dict:
    """Build a GenerateResponse envelope mirroring the generator proxy."""
    return {
        "success": True,
        "workflow_spec": spec,
        "error": None,
        "details": None,
        "raw_output": raw_output if raw_output is not None else json.dumps(spec),
    }


def _mock_proxy_failure(error: str, details: str = "", raw_output: str = "") -> dict:
    return {
        "success": False,
        "workflow_spec": None,
        "error": error,
        "details": details,
        "raw_output": raw_output,
    }


class TestGenerateWorkflowLocal:
    """Exercise the ``architect_provider="local"`` path."""

    @staticmethod
    def _patch_httpx(monkeypatch, response_payload: dict, status_code: int = 200,
                     captured: dict | None = None):
        """Replace httpx.AsyncClient in architect_client with one bound to a MockTransport."""
        async def handler(request: httpx.Request) -> httpx.Response:
            if captured is not None:
                captured["url"] = str(request.url)
                captured["body"] = json.loads(request.content)
                captured["headers"] = dict(request.headers)
            return httpx.Response(status_code, json=response_payload)

        transport = httpx.MockTransport(handler)

        class _BoundAsyncClient(httpx.AsyncClient):
            def __init__(self, **kwargs):
                kwargs.pop("transport", None)
                super().__init__(transport=transport, **kwargs)

        import app.services.architect_client as mod
        monkeypatch.setattr(mod.httpx, "AsyncClient", _BoundAsyncClient)

    async def test_happy_path_returns_workflow_spec(self, monkeypatch):
        captured: dict = {}
        self._patch_httpx(monkeypatch, _mock_proxy_success(SAMPLE_WORKFLOW_SPEC), captured=captured)

        client = ArchitectClient(provider="local", base_url="http://proxy:8002")
        result = await client.generate_workflow(
            intent="restart GPU workload",
            tools=SAMPLE_TOOLS,
            target_incident_types=["incident.gpu_memory_failure"],
        )

        # Returned spec is extracted from the envelope's workflow_spec field
        assert result == SAMPLE_WORKFLOW_SPEC

    async def test_request_body_shape(self, monkeypatch):
        """Local provider sends {system_prompt, user_message, max_tokens, temperature}."""
        captured: dict = {}
        self._patch_httpx(monkeypatch, _mock_proxy_success(SAMPLE_WORKFLOW_SPEC), captured=captured)

        client = ArchitectClient(provider="local", base_url="http://proxy:8002")
        await client.generate_workflow(intent="test", tools=SAMPLE_TOOLS)

        body = captured["body"]
        assert set(body.keys()) == {"system_prompt", "user_message", "max_tokens", "temperature"}
        assert body["temperature"] == 0.0
        assert body["max_tokens"] == 4096
        # Prompt content must still contain the tools — same _build_system_prompt as anthropic
        assert "fetch_pod_logs" in body["system_prompt"]
        assert "restart_workload" in body["system_prompt"]
        # User message carries the intent
        assert "test" in body["user_message"]

    async def test_hits_configured_endpoint(self, monkeypatch):
        captured: dict = {}
        self._patch_httpx(monkeypatch, _mock_proxy_success(SAMPLE_WORKFLOW_SPEC), captured=captured)

        client = ArchitectClient(
            provider="local", base_url="http://proxy:8002",
            local_endpoint="/custom/path",
        )
        await client.generate_workflow(intent="x", tools=[])

        assert captured["url"] == "http://proxy:8002/custom/path"

    async def test_proxy_failure_raises(self, monkeypatch):
        """If the proxy returns success=false, the client raises with the error details."""
        self._patch_httpx(monkeypatch, _mock_proxy_failure(
            error="JSON parsing failed",
            details="LLM output was truncated",
            raw_output="{\"workflow\": {\"st",
        ))

        client = ArchitectClient(provider="local", base_url="http://proxy:8002")
        with pytest.raises(RuntimeError) as exc:
            await client.generate_workflow(intent="broken", tools=SAMPLE_TOOLS)

        assert "JSON parsing failed" in str(exc.value)
        assert "truncated" in str(exc.value)

    async def test_proxy_success_without_workflow_spec_raises(self, monkeypatch):
        """success=true but missing workflow_spec is a protocol violation."""
        self._patch_httpx(monkeypatch, {
            "success": True, "workflow_spec": None, "error": None, "details": None, "raw_output": "",
        })

        client = ArchitectClient(provider="local", base_url="http://proxy:8002")
        with pytest.raises(RuntimeError):
            await client.generate_workflow(intent="x", tools=[])

    async def test_anthropic_path_still_works_with_provider_switch(self, monkeypatch):
        """Switching provider back to anthropic must hit the Messages API, not the proxy."""
        captured: dict = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_mock_anthropic_response(SAMPLE_WORKFLOW_SPEC))

        transport = httpx.MockTransport(handler)

        class _BoundAsyncClient(httpx.AsyncClient):
            def __init__(self, **kwargs):
                kwargs.pop("transport", None)
                super().__init__(transport=transport, **kwargs)

        import app.services.architect_client as mod
        monkeypatch.setattr(mod.httpx, "AsyncClient", _BoundAsyncClient)

        client = ArchitectClient(
            provider="anthropic", api_key="sk-test",
            base_url="http://anthropic-mock",
        )
        result = await client.generate_workflow(intent="test", tools=SAMPLE_TOOLS)

        assert "v1/messages" in captured["url"]
        assert captured["headers"]["x-api-key"] == "sk-test"
        assert "system" in captured["body"]
        assert result == SAMPLE_WORKFLOW_SPEC


class TestUnknownProvider:
    async def test_unknown_provider_raises(self):
        client = ArchitectClient(provider="made-up", api_key="x")
        with pytest.raises(ValueError) as exc:
            await client.generate_workflow(intent="x", tools=[])
        assert "architect_provider" in str(exc.value)
