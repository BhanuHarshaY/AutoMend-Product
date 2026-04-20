"""DynamicPlaybookExecutor — the universal playbook workflow (§21).

A single generic workflow that interprets any playbook DSL spec at runtime.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.temporal.activities import (
        load_playbook_activity,
        record_step_result_activity,
        resolve_incident_activity,
        update_incident_status_activity,
    )


@dataclass
class PlaybookExecutionInput:
    playbook_version_id: str
    incident_id: str
    incident_payload: dict
    execution_params: dict | None = None


@dataclass
class StepResult:
    step_id: str
    success: bool
    output: dict | None = None
    error: str | None = None


@workflow.defn
class DynamicPlaybookExecutor:
    """Executes any playbook by interpreting its DSL spec."""

    def __init__(self) -> None:
        self.step_outputs: dict[str, Any] = {}
        self.new_evidence_queue: list[dict] = []
        self.is_aborted: bool = False

    @workflow.signal
    async def new_evidence(self, evidence: dict) -> None:
        self.new_evidence_queue.append(evidence)

    @workflow.signal
    async def abort(self, reason: str) -> None:
        self.is_aborted = True

    @workflow.run
    async def run(self, input: PlaybookExecutionInput) -> dict:
        # Step 1: Load playbook spec
        playbook_data = await workflow.execute_activity(
            load_playbook_activity,
            args=[input.playbook_version_id],
            start_to_close_timeout=timedelta(seconds=30),
        )

        spec = playbook_data["workflow_spec"]
        expected_checksum = playbook_data["spec_checksum"]

        # Step 2: Validate checksum
        actual_checksum = hashlib.sha256(
            json.dumps(spec, sort_keys=True).encode()
        ).hexdigest()
        if actual_checksum != expected_checksum:
            raise ValueError("Playbook checksum mismatch — spec may have been tampered with")

        # Step 3: Update incident status
        await workflow.execute_activity(
            update_incident_status_activity,
            args=[input.incident_id, "in_progress"],
            start_to_close_timeout=timedelta(seconds=30),
        )

        # Step 4: Build context
        context: dict[str, Any] = {
            "incident": input.incident_payload,
            "params": {**self._get_default_params(spec), **(input.execution_params or {})},
            "steps": {},
            "env": {},
        }

        # Step 5: Execute steps
        steps = spec.get("steps", [])
        if not steps:
            return {"status": "completed", "steps_executed": []}

        step_index = {s["id"]: s for s in steps}
        current_step_id: str | None = steps[0]["id"]
        completed = False

        while current_step_id and not self.is_aborted:
            step = step_index.get(current_step_id)
            if not step:
                raise ValueError(f"Step '{current_step_id}' not found in playbook spec")

            result = await self._execute_step(step, context, input.incident_id)

            self.step_outputs[step["id"]] = result
            context["steps"][step["id"]] = {
                "output": result.output if result.success else {},
                "success": result.success,
                "error": result.error,
            }

            await workflow.execute_activity(
                record_step_result_activity,
                args=[input.incident_id, step["id"], result.success, result.output, result.error],
                start_to_close_timeout=timedelta(seconds=30),
            )

            # Determine next step
            if result.success:
                next_step = step.get("on_success")
                # Condition steps override next via branches
                if step["type"] == "condition" and result.output:
                    branch_next = result.output.get("next_step")
                    if branch_next:
                        next_step = branch_next

                if next_step is None:
                    # Default: go to next step in array
                    idx = steps.index(step)
                    if idx + 1 < len(steps):
                        next_step = steps[idx + 1]["id"]
                    else:
                        completed = True
                        next_step = None
                elif next_step not in step_index:
                    # Explicit end or unknown step ID → completed
                    completed = True
                    next_step = None
            else:
                next_step = step.get("on_failure")
                if next_step == "abort" or next_step is None:
                    self.is_aborted = True
                    next_step = None

            current_step_id = next_step

        # Step 6: Completion or abort
        if completed and not self.is_aborted:
            on_complete = spec.get("on_complete", {})
            if on_complete.get("resolve_incident", True):
                await workflow.execute_activity(
                    resolve_incident_activity,
                    args=[input.incident_id],
                    start_to_close_timeout=timedelta(seconds=30),
                )
            return {"status": "completed", "steps_executed": list(self.step_outputs.keys())}
        else:
            await workflow.execute_activity(
                update_incident_status_activity,
                args=[input.incident_id, "open"],
                start_to_close_timeout=timedelta(seconds=30),
            )
            return {"status": "aborted", "steps_executed": list(self.step_outputs.keys())}

    # ------------------------------------------------------------------
    # Step executors
    # ------------------------------------------------------------------

    async def _execute_step(self, step: dict, context: dict, incident_id: str) -> StepResult:
        step_type = step["type"]
        if step_type in ("action", "notification"):
            return await self._execute_action_step(step, context)
        elif step_type == "condition":
            return await self._execute_condition_step(step, context)
        elif step_type == "delay":
            return await self._execute_delay_step(step)
        elif step_type == "approval":
            return await self._execute_approval_step(step, context, incident_id)
        elif step_type == "parallel":
            return StepResult(step_id=step["id"], success=True, output={"note": "parallel TBD"})
        else:
            return StepResult(step_id=step["id"], success=False, error=f"Unknown step type: {step_type}")

    async def _execute_action_step(self, step: dict, context: dict) -> StepResult:
        tool_name = step.get("tool", "")
        resolved_input = self._resolve_templates(step.get("input", {}), context)
        timeout = self._parse_duration(step.get("timeout", "5m"))
        retry_config = step.get("retry", {})

        retry_policy = RetryPolicy(
            maximum_attempts=retry_config.get("max_attempts", 1),
            initial_interval=self._parse_duration(retry_config.get("initial_interval", "10s")),
            backoff_coefficient=2.0 if retry_config.get("backoff") == "exponential" else 1.0,
        )

        activity_name = f"{tool_name}_activity"

        try:
            output = await workflow.execute_activity(
                activity_name,
                args=[resolved_input],
                start_to_close_timeout=timeout,
                retry_policy=retry_policy,
            )
            return StepResult(step_id=step["id"], success=True, output=output)
        except Exception as e:
            return StepResult(step_id=step["id"], success=False, error=str(e))

    async def _execute_condition_step(self, step: dict, context: dict) -> StepResult:
        condition_expr = step.get("condition", "False")
        resolved = self._resolve_template_string(condition_expr, context)

        try:
            result = self._safe_eval(resolved)
            branches = step.get("branches", {})
            next_step = branches.get("true" if result else "false")
            return StepResult(
                step_id=step["id"],
                success=True,
                output={"condition_result": result, "branch": "true" if result else "false", "next_step": next_step},
            )
        except Exception as e:
            return StepResult(step_id=step["id"], success=False, error=f"Condition eval failed: {e}")

    async def _execute_delay_step(self, step: dict) -> StepResult:
        duration = self._parse_duration(step.get("duration", "1m"))
        await workflow.sleep(duration)
        return StepResult(step_id=step["id"], success=True, output={"waited": str(duration)})

    async def _execute_approval_step(self, step: dict, context: dict, incident_id: str) -> StepResult:
        timeout = self._parse_duration(step.get("approval_timeout", "30m"))
        message = self._resolve_template_string(step.get("approval_message", "Approval required"), context)

        try:
            await workflow.execute_activity(
                "slack_approval_activity",
                args=[{
                    "channel": step.get("approval_channel", "#incident-ops"),
                    "message": message,
                    "timeout_minutes": int(timeout.total_seconds() / 60),
                    "incident_id": incident_id,
                }],
                start_to_close_timeout=timeout + timedelta(minutes=1),
            )
            return StepResult(step_id=step["id"], success=True, output={"approved": True})
        except Exception as e:
            return StepResult(step_id=step["id"], success=False, error=f"Approval failed/rejected: {e}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_templates(self, obj: Any, context: dict) -> Any:
        if isinstance(obj, str):
            return self._resolve_template_string(obj, context)
        elif isinstance(obj, dict):
            return {k: self._resolve_templates(v, context) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._resolve_templates(item, context) for item in obj]
        return obj

    def _resolve_template_string(self, s: str, context: dict) -> str:
        def replace_match(match: re.Match) -> str:
            path = match.group(1)
            parts = path.split(".")
            value: Any = context
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part)
                elif isinstance(value, list) and part.isdigit():
                    value = value[int(part)]
                else:
                    return match.group(0)
                if value is None:
                    return ""
            return str(value) if value is not None else ""

        return re.sub(r"\$\{([^}]+)\}", replace_match, s)

    @staticmethod
    def _parse_duration(duration_str: str | timedelta) -> timedelta:
        if isinstance(duration_str, timedelta):
            return duration_str
        unit = duration_str[-1]
        value = int(duration_str[:-1])
        return {
            "s": timedelta(seconds=value),
            "m": timedelta(minutes=value),
            "h": timedelta(hours=value),
            "d": timedelta(days=value),
        }.get(unit, timedelta(minutes=value))

    @staticmethod
    def _get_default_params(spec: dict) -> dict:
        params: dict[str, Any] = {}
        for name, config in spec.get("parameters", {}).items():
            if "default" in config:
                params[name] = config["default"]
        return params

    @staticmethod
    def _safe_eval(expression: str) -> bool:
        import ast
        try:
            tree = ast.parse(expression, mode="eval")
            for node in ast.walk(tree):
                if not isinstance(node, (
                    ast.Expression, ast.Compare, ast.Constant,
                    ast.BoolOp, ast.And, ast.Or,
                    ast.UnaryOp, ast.Not,
                    ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
                )):
                    raise ValueError(f"Disallowed node: {type(node).__name__}")
            return bool(eval(compile(tree, "<condition>", "eval")))
        except Exception:
            return False
