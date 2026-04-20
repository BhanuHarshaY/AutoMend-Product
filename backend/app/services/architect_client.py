"""Architect client — generates playbook workflow specs from natural language (§16).

Supports two provider backends, selected via ``settings.architect_provider``:

* ``"anthropic"`` — calls Anthropic's Messages API at ``{base_url}/v1/messages``
  with ``x-api-key`` auth. The response is an Anthropic ``content[]`` array of
  text blocks; the first block is expected to contain the JSON workflow spec,
  optionally wrapped in markdown code fences.
* ``"local"`` — calls the Qwen vLLM proxy from
  ``inference_backend/GeneratorModel/generatorModelAPI/`` at
  ``{base_url}{local_endpoint}`` with a ``{system_prompt, user_message,
  max_tokens, temperature}`` body. The response is the proxy's ``GenerateResponse``
  envelope (``{success, workflow_spec, error, details, raw_output}``); on
  ``success=false`` the client raises with the proxy's error details.

Prompt content is **identical** across providers — ``_build_system_prompt`` and
``_build_user_prompt`` assemble the same strings, only the HTTP envelope
differs. See DECISION-022 for the rationale.
"""

from __future__ import annotations

import json
import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class ArchitectClient:
    """Generates playbook workflow specs from natural-language intent."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        local_endpoint: str | None = None,
    ) -> None:
        settings = get_settings()
        self.provider = (provider or settings.architect_provider).lower()
        self.api_key = api_key if api_key is not None else settings.architect_api_key
        self.base_url = base_url or settings.architect_api_base_url
        self.model = model or settings.architect_model
        self.local_endpoint = local_endpoint or settings.architect_local_endpoint

    async def generate_workflow(
        self,
        intent: str,
        tools: list[dict],
        example_playbooks: list[dict] | None = None,
        policies: list[str] | None = None,
        target_incident_types: list[str] | None = None,
    ) -> dict:
        """Generate a playbook workflow spec from user intent + context.

        Returns the parsed JSON workflow spec dict.
        """
        system_prompt = self._build_system_prompt(tools, example_playbooks, policies)
        user_prompt = self._build_user_prompt(intent, target_incident_types)

        if self.provider == "local":
            return await self._call_local(system_prompt, user_prompt)
        if self.provider == "anthropic":
            return await self._call_anthropic(system_prompt, user_prompt)
        if self.provider == "gemini":
            return await self._call_gemini(system_prompt, user_prompt)
        raise ValueError(
            f"Unknown architect_provider: {self.provider!r}. "
            f"Valid values are 'anthropic', 'local', and 'gemini'."
        )

    # ------------------------------------------------------------------
    # Provider backends
    # ------------------------------------------------------------------

    async def _call_anthropic(self, system_prompt: str, user_prompt: str) -> dict:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{self.base_url}/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 4096,
                    "system": system_prompt,
                    "messages": [
                        {"role": "user", "content": user_prompt},
                    ],
                },
            )
            response.raise_for_status()
            data = response.json()

            text_content = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text_content += block["text"]

            return _extract_json(text_content)

    async def _call_gemini(self, system_prompt: str, user_prompt: str) -> dict:
        """Call Gemini via Google AI Studio's generateContent API.

        Model defaults to `gemini-2.5-pro`; override via AUTOMEND_ARCHITECT_MODEL.
        API key is the same `AUTOMEND_ARCHITECT_API_KEY` setting (a Google AI
        Studio key from https://aistudio.google.com/apikey).
        """
        model = self.model or "gemini-2.5-pro"
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={self.api_key}"
        )
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                url,
                json={
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": [
                        {"role": "user", "parts": [{"text": user_prompt}]},
                    ],
                    "generationConfig": {
                        "temperature": 0.0,
                        # Gemini 2.5 Flash supports up to 65k output tokens.
                        # Bumped from 4k → 16k because real AutoMend specs
                        # with RAG-selected tools + examples can easily
                        # overflow 4k and cause truncation mid-JSON.
                        "maxOutputTokens": 16384,
                        "responseMimeType": "application/json",
                    },
                },
            )
            response.raise_for_status()
            data = response.json()

        # Extract the text across all candidate parts. Gemini returns a list
        # of candidates each with `content.parts[*].text`; concatenate so we
        # don't drop anything if the model splits JSON across parts.
        text_content = ""
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if "text" in part:
                    text_content += part["text"]
        if not text_content:
            raise RuntimeError(
                "Gemini returned no text content. Full response: "
                f"{json.dumps(data)[:500]}"
            )
        return _extract_json(text_content)

    async def _call_local(self, system_prompt: str, user_prompt: str) -> dict:
        url = f"{self.base_url}{self.local_endpoint}"
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                url,
                json={
                    "system_prompt": system_prompt,
                    "user_message": user_prompt,
                    "max_tokens": 4096,
                    "temperature": 0.0,
                },
            )
            response.raise_for_status()
            envelope = response.json()

        if not envelope.get("success"):
            error = envelope.get("error") or "generator proxy reported failure"
            details = envelope.get("details") or ""
            raw = envelope.get("raw_output") or ""
            msg = f"{error}: {details}" if details else error
            if raw:
                logger.warning("Local architect proxy failed. Raw output: %s", raw[:500])
            raise RuntimeError(msg)

        spec = envelope.get("workflow_spec")
        if not isinstance(spec, dict):
            raise RuntimeError(
                "Local architect proxy returned success=true but no workflow_spec dict"
            )
        return spec

    # ------------------------------------------------------------------
    # Prompt builders (provider-agnostic)
    # ------------------------------------------------------------------

    def _build_system_prompt(
        self,
        tools: list[dict],
        example_playbooks: list[dict] | None = None,
        policies: list[str] | None = None,
    ) -> str:
        tools_section = "## Available Tools\n\n"
        for tool in tools:
            tools_section += f"### {tool['name']}\n"
            tools_section += f"Description: {tool['description']}\n"
            tools_section += f"Side effect level: {tool.get('side_effect_level', 'unknown')}\n"
            tools_section += f"Input schema: {json.dumps(tool.get('input_schema', {}))}\n"
            tools_section += f"Required approvals: {tool.get('required_approvals', 0)}\n\n"

        examples_section = ""
        if example_playbooks:
            examples_section = "## Example Playbooks\n\n"
            for pb in example_playbooks[:3]:
                examples_section += f"### {pb.get('name', 'Unnamed')}\n"
                examples_section += f"```json\n{json.dumps(pb.get('workflow_spec', {}), indent=2)}\n```\n\n"

        policies_section = ""
        if policies:
            policies_section = "## Policies\n\n"
            for p in policies:
                policies_section += f"- {p}\n"

        return f"""You are an infrastructure automation architect. You generate workflow specifications
in a strict JSON DSL format for incident remediation playbooks.

RULES:
1. Only use tools from the Available Tools list below. Do not invent tools.
2. Every tool reference must use the exact 'name' field from the tool list.
3. Tools with side_effect_level 'destructive' or 'write' MUST be preceded by an approval step
   unless the intent explicitly says to auto-remediate without approval.
4. Include appropriate retry and timeout policies.
5. Include error handling steps (on_failure transitions).
6. Output ONLY valid JSON conforming to the playbook DSL. No explanation, no markdown.

{tools_section}
{examples_section}
{policies_section}

## Playbook DSL Schema

The output must be a JSON object with this structure:

{{
  "name": "string - playbook name",
  "description": "string - what this playbook does",
  "version": "string - semver",
  "trigger": {{
    "incident_types": ["string - incident types this handles"],
    "severity_filter": ["string - optional severity filter"]
  }},
  "steps": [
    {{
      "id": "string - unique step identifier",
      "name": "string - human readable name",
      "type": "action | approval | condition | delay | parallel | notification",
      "tool": "string - tool name from registry (for action type)",
      "input": {{ "key": "value or ${{incident.field}}" }},
      "timeout": "duration string e.g. '5m', '1h'",
      "on_success": "next_step_id",
      "on_failure": "error_step_id or 'abort'"
    }}
  ]
}}
"""

    def _build_user_prompt(
        self,
        intent: str,
        target_incident_types: list[str] | None = None,
    ) -> str:
        prompt = f"Generate a playbook workflow spec for the following intent:\n\n{intent}"
        if target_incident_types:
            prompt += f"\n\nTarget incident types: {', '.join(target_incident_types)}"
        return prompt


# ---------------------------------------------------------------------------
# JSON extraction helper
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response, handling code fences + preamble prose."""
    cleaned = text.strip()

    # Strip markdown code fences if present
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if "```" in cleaned:
            cleaned = cleaned.rsplit("```", 1)[0]
        cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        # Gemini-2.5-flash sometimes emits thinking prose before/after the JSON
        # object even when `responseMimeType: application/json` is set. Fall back
        # to the widest `{ ... }` substring.
        first = cleaned.find("{")
        last = cleaned.rfind("}")
        if first != -1 and last > first:
            snippet = cleaned[first:last + 1]
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                pass
        # Log a preview of what we got so operators can see what the model produced.
        preview = cleaned[:1500] + ("…" if len(cleaned) > 1500 else "")
        logger.warning("Failed to parse architect response as JSON. Preview: %s", preview)
        raise RuntimeError(
            f"Architect response was not valid JSON ({e}). "
            f"First 300 chars: {cleaned[:300]!r}"
        ) from e
