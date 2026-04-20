"""
FastAPI proxy that sits between the AutoMend core backend and the vLLM server.

Phase 10.2 (2026-04-14): the proxy is schema-agnostic. It does not know
about the tool registry, the DSL, or per-tool parameter shapes — all of
that logic lives in the core's ArchitectClient and _validate_spec.

Flow:
  core backend  --POST /generate_workflow-->  proxy  --POST /v1/chat/completions-->  vLLM
                <----{success, workflow_spec}----------{raw assistant content}-----
"""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import FastAPI

from app.guardrails import parse_llm_output
from app.schemas.workflow import GenerateRequest, GenerateResponse

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

VLLM_URL = os.getenv("VLLM_URL", "http://vllm-generator:8001")
VLLM_CHAT_ENDPOINT = f"{VLLM_URL}/v1/chat/completions"
VLLM_MODEL_NAME = os.getenv("VLLM_MODEL_NAME", "/models/fused_model")
VLLM_TIMEOUT_SECONDS = float(os.getenv("VLLM_TIMEOUT_SECONDS", "60"))


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="AutoMend Generator Proxy", version="2.0.0")


@app.get("/health")
def health() -> dict:
    return {"status": "healthy", "vllm_url": VLLM_URL}


@app.post("/generate_workflow", response_model=GenerateResponse)
async def generate_workflow(body: GenerateRequest) -> GenerateResponse:
    # ---- 1. Build ChatML payload ----------------------------------------
    chat_payload = {
        "model": VLLM_MODEL_NAME,
        "messages": [
            {"role": "system", "content": body.system_prompt},
            {"role": "user", "content": body.user_message},
        ],
        "temperature": body.temperature,
        "max_tokens": body.max_tokens,
    }

    # ---- 2. Call vLLM ---------------------------------------------------
    try:
        async with httpx.AsyncClient(timeout=VLLM_TIMEOUT_SECONDS) as client:
            vllm_resp = await client.post(VLLM_CHAT_ENDPOINT, json=chat_payload)
    except httpx.ConnectError:
        logger.error("Cannot connect to vLLM at %s", VLLM_URL)
        return GenerateResponse(
            success=False,
            error="vLLM connection failed",
            details=f"Could not connect to vLLM at {VLLM_URL}. Is the server running?",
        )
    except httpx.TimeoutException:
        logger.error("vLLM request timed out (%s)", VLLM_URL)
        return GenerateResponse(
            success=False,
            error="vLLM request timed out",
            details=f"The vLLM server did not respond within {VLLM_TIMEOUT_SECONDS}s.",
        )
    except httpx.HTTPError as exc:
        logger.error("HTTP error calling vLLM: %s", exc)
        return GenerateResponse(
            success=False,
            error="vLLM HTTP error",
            details=str(exc),
        )

    if vllm_resp.status_code != 200:
        logger.warning(
            "vLLM returned HTTP %s: %s",
            vllm_resp.status_code,
            vllm_resp.text[:300],
        )
        return GenerateResponse(
            success=False,
            error=f"vLLM returned HTTP {vllm_resp.status_code}",
            details=vllm_resp.text[:500],
        )

    # ---- 3. Extract assistant content -----------------------------------
    try:
        vllm_body = vllm_resp.json()
        raw_output = vllm_body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        logger.error("Unexpected vLLM response structure: %s", exc)
        return GenerateResponse(
            success=False,
            error="Unexpected vLLM response format",
            details=str(exc),
            raw_output=vllm_resp.text[:1000],
        )

    finish_reason = (
        vllm_body.get("choices", [{}])[0].get("finish_reason", "unknown")
    )
    if finish_reason == "length":
        logger.warning(
            "vLLM output was truncated (finish_reason=length). "
            "Guardrails will attempt bracket repair."
        )

    # ---- 4. Parse JSON (with repair) ------------------------------------
    parsed = parse_llm_output(raw_output)
    if parsed is None:
        logger.warning("All JSON parse attempts failed for vLLM output")
        return GenerateResponse(
            success=False,
            error="JSON parsing failed",
            details=(
                "Could not parse LLM output as valid JSON after repair "
                "attempts (direct parse, markdown strip, trailing-comma "
                "fix, bracket closing)."
            ),
            raw_output=raw_output,
        )

    # ---- 5. Return the parsed dict as-is --------------------------------
    # No schema validation happens here. The AutoMend core backend runs the
    # returned workflow_spec through its own _validate_spec, which knows the
    # full tool registry and DSL. The proxy is intentionally dumb.
    logger.info("Workflow spec returned (keys=%s)", sorted(parsed.keys()))
    return GenerateResponse(
        success=True,
        workflow_spec=parsed,
        raw_output=raw_output,
    )
