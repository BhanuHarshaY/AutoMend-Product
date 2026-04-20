# GeneratorModel API — Schema-Agnostic vLLM Proxy (Track B)

Lightweight FastAPI proxy that sits between the **AutoMend core backend** and the **vLLM server** (Qwen2.5-1.5B + LoRA, Model 2). It forwards a system prompt + user message to vLLM, repairs the raw LLM string into valid JSON, and returns the parsed dict as-is.

> **Phase 10.2 (2026-04-14):** the proxy no longer enforces a tool registry or any per-step schema. The hardcoded 6-tool list (scale_deployment / restart_rollout / undo_rollout / send_notification / request_approval / trigger_webhook) is gone. Tool selection, DSL shape, and workflow validation all live in the core backend now (`backend/app/api/routes_design.py::_validate_spec`, `backend/app/services/architect_client.py`). See DECISION-020 in the core repo's `DECISIONS.md`.

## Architecture

```
                         Port 8002                       Port 8001
 Core Backend  ──POST──>  Proxy   ──POST──>  vLLM (/v1/chat/completions)
               <──JSON──  (this)  <──raw str──  Qwen2.5-1.5B
                          │
                  ┌───────┴────────┐
                  │  guardrails.py │  parse + repair raw LLM string
                  │  (passthrough) │  NO schema validation — core owns that
                  └────────────────┘
```

The proxy:
1. Receives `{system_prompt, user_message, max_tokens, temperature}` from the core
2. Builds a ChatML payload with those two strings as the system and user messages (no tool-registry preamble — the core's `ArchitectClient._build_system_prompt` already baked it into `system_prompt`)
3. Forwards to vLLM on port 8001
4. Extracts the assistant message content
5. Runs a 5-stage JSON repair pipeline (markdown fences, first-balanced-object, trailing commas, unclosed brackets)
6. Returns `{"success": true, "workflow_spec": <parsed dict>, "raw_output": "<original LLM string>"}` or `{"success": false, "error": "...", "details": "...", "raw_output": "..."}`

Whatever dict the LLM produces — core's `PlaybookSpec` (§19 DSL), a double-nested `{workflow: {workflow: {...}}}`, a one-off experimental shape — the proxy returns it unchanged. The core validates it.

## File Structure

```
generatorModelAPI/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI application (port 8002)
│   ├── guardrails.py        # JSON parsing + repair pipeline (unchanged)
│   └── schemas/
│       ├── __init__.py
│       └── workflow.py      # GenerateRequest + GenerateResponse (minimal)
├── tests/
│   ├── __init__.py
│   ├── conftest.py          # Sample prompt fixture + mock vLLM builder
│   ├── test_guardrails.py   # JSON-repair unit tests (unchanged)
│   ├── test_schemas.py      # GenerateRequest / GenerateResponse validation
│   └── test_proxy.py        # Passthrough + repair + error-path route tests
├── requirements.txt
└── README.md                # This file
```

## Request / Response Contract

### `POST /generate_workflow`

Request:
```json
{
  "system_prompt": "You are an infrastructure automation architect... [tools, DSL schema, examples, policies]",
  "user_message": "GPU memory pressure on the recommendation service",
  "max_tokens": 4096,
  "temperature": 0.0
}
```

Response (success):
```json
{
  "success": true,
  "workflow_spec": {
    "name": "...",
    "version": "1.0.0",
    "trigger": {"incident_types": ["incident.memory"]},
    "steps": [{"id": "s1", "type": "action", "tool": "scale_deployment", "input": {...}}]
  },
  "error": null,
  "details": null,
  "raw_output": "<the original LLM text>"
}
```

Response (failure — JSON parse / vLLM connection / HTTP error):
```json
{
  "success": false,
  "workflow_spec": null,
  "error": "JSON parsing failed",
  "details": "...",
  "raw_output": "<the original LLM text>"
}
```

The proxy **always returns HTTP 200** unless you send a malformed request (422 for missing/empty `system_prompt` or `user_message`).

### `GET /health`

```json
{"status": "healthy", "vllm_url": "http://vllm-generator:8001"}
```

## Why Temperature Must Stay 0.0

The fine-tuned model produces malformed JSON at non-zero temperatures. The default is 0.0 and the schema allows overrides, but bumping it up is at your own risk.

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `VLLM_URL` | `http://vllm-generator:8001` | Base URL of the vLLM server |
| `VLLM_MODEL_NAME` | `/models/fused_model` | `model` field sent in the ChatML payload — vLLM returns 404 on a mismatch |
| `VLLM_TIMEOUT_SECONDS` | `60` | HTTP timeout for the vLLM call |

## Prerequisites

- Python 3.10+
- Access to a running vLLM instance (or use the mocked tests without one)

## Setup

```bash
cd inference_backend/GeneratorModel/generatorModelAPI
pip install -r requirements.txt
VLLM_URL=http://localhost:8001 uvicorn app.main:app --host 0.0.0.0 --port 8002
```

## Running Tests

```bash
pip install pytest pytest-asyncio httpx
pytest tests/ -v
```

69 tests:
- **34 guardrails** — markdown stripping, first-object extraction, trailing-comma + bracket repair, parse pipeline
- **11 schemas** — `GenerateRequest` / `GenerateResponse` validation (required fields, ranges, extra-field tolerance)
- **24 proxy** — passthrough (arbitrary tool names / shapes come through unchanged, legacy double-nested shape, empty steps), guardrails integration, JSON-parse failures, vLLM connection failures, forwarded-prompt verification, request validation

All vLLM HTTP calls are mocked — no GPU or running vLLM instance needed.

## Integration With the Core Backend

The core's `ArchitectClient` (at `backend/app/services/architect_client.py`) builds the system prompt by:

1. Running the user's intent through `VectorSearchService.search_tools` (pgvector RAG) to select the top-N relevant tools from the `tools` table
2. Concatenating those tool descriptions + example playbooks (RAG-selected) + DSL schema + policies into the system prompt
3. POSTing `{system_prompt, user_message}` to this proxy

This proxy forwards that string verbatim. When the model is retrained on a different tool registry or DSL, nothing in this service needs to change — it's just pipes and guardrails.

## What Is NOT Implemented Here

- Tool registry — lives in the core's `tools` Postgres table
- DSL schema — lives in `backend/app/domain/playbooks.py::PlaybookSpec`
- `_validate_spec` — lives in `backend/app/api/routes_design.py`
- RAG tool selection — `backend/app/services/vector_search_service.py`
- Confidence gating, audit trail, auth, human-in-the-loop approvals — all core concerns
