"""Unit tests for the passthrough GenerateRequest / GenerateResponse schemas.

The old per-tool param models and 6-tool Literal live elsewhere now — in the
AutoMend core backend's tool registry + _validate_spec. See DECISION-020.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.workflow import GenerateRequest, GenerateResponse


class TestGenerateRequest:
    def test_minimal_valid(self):
        req = GenerateRequest(system_prompt="sys", user_message="do it")
        assert req.system_prompt == "sys"
        assert req.user_message == "do it"
        assert req.temperature == 0.0
        assert req.max_tokens == 4096

    def test_empty_system_prompt_rejected(self):
        with pytest.raises(ValidationError):
            GenerateRequest(system_prompt="", user_message="x")

    def test_empty_user_message_rejected(self):
        with pytest.raises(ValidationError):
            GenerateRequest(system_prompt="x", user_message="")

    def test_missing_fields_rejected(self):
        with pytest.raises(ValidationError):
            GenerateRequest(system_prompt="sys")  # type: ignore[call-arg]
        with pytest.raises(ValidationError):
            GenerateRequest(user_message="u")  # type: ignore[call-arg]

    def test_custom_temperature_and_max_tokens(self):
        req = GenerateRequest(
            system_prompt="s", user_message="u",
            temperature=0.5, max_tokens=2048,
        )
        assert req.temperature == 0.5
        assert req.max_tokens == 2048

    def test_temperature_range(self):
        with pytest.raises(ValidationError):
            GenerateRequest(system_prompt="s", user_message="u", temperature=-0.1)
        with pytest.raises(ValidationError):
            GenerateRequest(system_prompt="s", user_message="u", temperature=2.1)

    def test_max_tokens_range(self):
        with pytest.raises(ValidationError):
            GenerateRequest(system_prompt="s", user_message="u", max_tokens=0)
        with pytest.raises(ValidationError):
            GenerateRequest(system_prompt="s", user_message="u", max_tokens=100_000)

    def test_extra_fields_ignored(self):
        """Legacy callers might still send fields that no longer exist."""
        req = GenerateRequest(
            system_prompt="s",
            user_message="u",
            system_context="legacy field",   # old field name, silently ignored
            tools=["x"],
        )
        assert req.system_prompt == "s"


class TestGenerateResponse:
    def test_success_shape(self):
        resp = GenerateResponse(
            success=True,
            workflow_spec={"anything": "here", "nested": {"deep": 1}},
        )
        assert resp.success is True
        assert resp.workflow_spec == {"anything": "here", "nested": {"deep": 1}}
        assert resp.error is None

    def test_failure_shape(self):
        resp = GenerateResponse(
            success=False,
            error="JSON parsing failed",
            details="...",
            raw_output="<garbage>",
        )
        assert resp.success is False
        assert resp.workflow_spec is None

    def test_workflow_spec_accepts_any_shape(self):
        """This is the whole point of the rewrite — no tool-specific schema."""
        for spec in [
            {"steps": []},                                   # empty
            {"name": "x", "version": "1.0.0", "steps": [{"id": "s1", "type": "action"}]},  # core §19
            {"workflow": {"steps": [{"step_id": 1}]}},        # old Qwen shape
            {"totally": "unrelated", "keys": ["a", "b"]},    # arbitrary
        ]:
            resp = GenerateResponse(success=True, workflow_spec=spec)
            assert resp.workflow_spec == spec
