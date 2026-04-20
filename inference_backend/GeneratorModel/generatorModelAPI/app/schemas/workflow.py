"""
Pydantic v2 schemas for the Generator proxy.

Phase 10.2 (2026-04-14): the proxy is now schema-agnostic. It does NOT know
about the tool registry, the workflow DSL, or per-tool parameter shapes —
that all lives in the AutoMend core backend (see
`backend/app/api/routes_design.py::_validate_spec` and
`backend/app/services/architect_client.py`). The core RAG-selects relevant
tools per request, bakes them into the system prompt it sends here, and
runs the returned dict through its own validator.

See DECISION-020 in the core repo's DECISIONS.md.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GenerateRequest(BaseModel):
    """Payload the core backend sends to the proxy.

    The ``system_prompt`` is assembled by
    ``ArchitectClient._build_system_prompt`` on the core side and already
    contains the tool registry + example playbooks + DSL schema + policies.
    The proxy does not inspect it — it just forwards it to vLLM as the
    ChatML ``system`` message.
    """

    model_config = ConfigDict(extra="ignore")

    system_prompt: str = Field(
        ...,
        min_length=1,
        description="Full system prompt (tools + DSL schema + examples + policies).",
    )
    user_message: str = Field(
        ...,
        min_length=1,
        description="The user's intent (natural-language incident description).",
    )
    max_tokens: int = Field(
        default=4096,
        ge=1,
        le=16384,
        description="vLLM ``max_tokens`` for the generation.",
    )
    temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description=(
            "Sampling temperature. Keep at 0.0 for JSON-reliable generation — "
            "the fine-tuned model produces malformed JSON at non-zero temps."
        ),
    )


class GenerateResponse(BaseModel):
    """Unified envelope — the proxy always returns HTTP 200 with a success flag.

    ``workflow_spec`` is the parsed JSON exactly as the LLM produced it (after
    guardrails repair). The proxy does NOT normalise, validate, or reshape it.
    Callers should run it through their own schema validator before use.
    """

    success: bool
    workflow_spec: dict | None = None
    error: str | None = None
    details: str | None = None
    raw_output: str | None = None
