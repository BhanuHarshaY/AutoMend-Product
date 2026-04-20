"""Shared pytest fixtures for the Generator proxy tests.

Phase 10.2: the old workflow fixtures (hardcoded 6-tool shape) were deleted
with the schema. Proxy is now passthrough — tests assert the parsed dict
comes through unchanged, regardless of its internal shape.
"""

from __future__ import annotations

from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Sample prompts (opaque to the proxy — used just to fill request bodies)
# ---------------------------------------------------------------------------

SAMPLE_SYSTEM_PROMPT = (
    "You are an infrastructure automation architect. Output JSON only.\n\n"
    "## Available Tools\n\n### scale_deployment\n...\n\n## Playbook DSL Schema\n..."
)

SAMPLE_USER_MESSAGE = "GPU memory pressure on the recommendation service. Scale up replicas."


@pytest.fixture()
def sample_request_body() -> dict:
    """Minimal valid GenerateRequest body for tests."""
    return {
        "system_prompt": SAMPLE_SYSTEM_PROMPT,
        "user_message": SAMPLE_USER_MESSAGE,
    }


# ---------------------------------------------------------------------------
# Mock vLLM response builder
# ---------------------------------------------------------------------------

def make_vllm_response(
    content: str,
    status_code: int = 200,
    finish_reason: str = "stop",
) -> dict[str, Any]:
    """Build a dict matching the vLLM /v1/chat/completions response shape."""
    return {
        "status_code": status_code,
        "body": {
            "id": "cmpl-test",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "/models/fused_model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        },
    }
