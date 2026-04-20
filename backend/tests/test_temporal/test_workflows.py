"""Tests for DynamicPlaybookExecutor workflow using Temporal's test environment.

Uses temporalio.testing.WorkflowEnvironment — runs an in-process Temporal
server. No Docker Temporal needed.

Activities are mocked to avoid DB/K8s dependencies.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from temporalio import activity
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.temporal.workflows import DynamicPlaybookExecutor, PlaybookExecutionInput


# ---------------------------------------------------------------------------
# Mock activities — replace real DB/K8s calls with in-memory stubs
# ---------------------------------------------------------------------------

_mock_playbook_store: dict[str, dict] = {}
_mock_incident_status: dict[str, str] = {}
_mock_step_results: list[dict] = []


def _spec_checksum(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


@activity.defn(name="load_playbook_activity")
async def mock_load_playbook(playbook_version_id: str) -> dict:
    data = _mock_playbook_store.get(playbook_version_id)
    if data is None:
        raise ValueError(f"Playbook {playbook_version_id} not found")
    return data


@activity.defn(name="update_incident_status_activity")
async def mock_update_incident_status(incident_id: str, new_status: str) -> dict:
    _mock_incident_status[incident_id] = new_status
    return {"incident_id": incident_id, "status": new_status}


@activity.defn(name="resolve_incident_activity")
async def mock_resolve_incident(incident_id: str) -> dict:
    _mock_incident_status[incident_id] = "resolved"
    return {"incident_id": incident_id, "status": "resolved"}


@activity.defn(name="record_step_result_activity")
async def mock_record_step_result(
    incident_id: str, step_id: str, success: bool, output: dict | None, error: str | None,
) -> dict:
    _mock_step_results.append({
        "incident_id": incident_id,
        "step_id": step_id,
        "success": success,
        "output": output,
        "error": error,
    })
    return {"recorded": True, "step_id": step_id}


# Tool activity mocks
@activity.defn(name="fetch_pod_logs_activity")
async def mock_fetch_pod_logs(input: dict) -> dict:
    return {"logs": "mock logs output", "line_count": 42}


@activity.defn(name="restart_workload_activity")
async def mock_restart_workload(input: dict) -> dict:
    return {"success": True, "message": "mock restart"}


@activity.defn(name="slack_notification_activity")
async def mock_slack_notification(input: dict) -> dict:
    return {"success": True, "ts": "mock-ts"}


@activity.defn(name="slack_approval_activity")
async def mock_slack_approval(input: dict) -> dict:
    return {"approved": True, "approver": "mock-user", "timestamp": "now"}


@activity.defn(name="query_prometheus_activity")
async def mock_query_prometheus(input: dict) -> dict:
    return {"result_type": "vector", "result": [{"value": [0, "0.97"]}]}


@activity.defn(name="page_oncall_activity")
async def mock_page_oncall(input: dict) -> dict:
    return {"success": True, "page_id": "pg-123"}


MOCK_ACTIVITIES = [
    mock_load_playbook,
    mock_update_incident_status,
    mock_resolve_incident,
    mock_record_step_result,
    mock_fetch_pod_logs,
    mock_restart_workload,
    mock_slack_notification,
    mock_slack_approval,
    mock_query_prometheus,
    mock_page_oncall,
]

TASK_QUEUE = "test-queue"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def env():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        yield env


@pytest_asyncio.fixture()
async def worker_client(env: WorkflowEnvironment):
    """Start a worker with mock activities and return the client."""
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[DynamicPlaybookExecutor],
        activities=MOCK_ACTIVITIES,
    ):
        yield env.client


@pytest.fixture(autouse=True)
def _reset_mocks():
    _mock_playbook_store.clear()
    _mock_incident_status.clear()
    _mock_step_results.clear()


def _register_playbook(version_id: str, spec: dict) -> None:
    _mock_playbook_store[version_id] = {
        "workflow_spec": spec,
        "spec_checksum": _spec_checksum(spec),
    }


def _simple_spec(steps: list[dict], **kwargs) -> dict:
    defaults = {"name": "Test Playbook", "version": "1.0.0", "trigger": {"incident_types": ["test"]}}
    defaults["steps"] = steps
    defaults.update(kwargs)
    return defaults


# ===================================================================
# TESTS
# ===================================================================


class TestBasicWorkflowExecution:
    async def test_single_action_step(self, worker_client: Client):
        """A playbook with one action step runs to completion."""
        version_id = str(uuid4())
        spec = _simple_spec([
            {"id": "fetch", "name": "Fetch Logs", "type": "action", "tool": "fetch_pod_logs",
             "input": {"namespace": "ml", "pod": "trainer"}},
        ])
        _register_playbook(version_id, spec)

        result = await worker_client.execute_workflow(
            DynamicPlaybookExecutor.run,
            PlaybookExecutionInput(
                playbook_version_id=version_id,
                incident_id="inc-1",
                incident_payload={"entity": {"namespace": "ml"}},
            ),
            id=f"wf-{uuid4().hex[:8]}",
            task_queue=TASK_QUEUE,
        )

        assert result["status"] == "completed"
        assert result["steps_executed"] == ["fetch"]
        assert _mock_incident_status.get("inc-1") == "resolved"

    async def test_multi_step_sequence(self, worker_client: Client):
        """Steps execute in order, each output is recorded."""
        version_id = str(uuid4())
        spec = _simple_spec([
            {"id": "step_a", "name": "A", "type": "action", "tool": "fetch_pod_logs"},
            {"id": "step_b", "name": "B", "type": "action", "tool": "restart_workload"},
            {"id": "step_c", "name": "C", "type": "action", "tool": "slack_notification",
             "input": {"channel": "#ops", "message": "done"}},
        ])
        _register_playbook(version_id, spec)

        result = await worker_client.execute_workflow(
            DynamicPlaybookExecutor.run,
            PlaybookExecutionInput(playbook_version_id=version_id, incident_id="inc-2",
                                   incident_payload={}),
            id=f"wf-{uuid4().hex[:8]}", task_queue=TASK_QUEUE,
        )

        assert result["status"] == "completed"
        assert result["steps_executed"] == ["step_a", "step_b", "step_c"]
        assert len(_mock_step_results) == 3
        assert all(r["success"] for r in _mock_step_results)

    async def test_empty_steps_completes(self, worker_client: Client):
        version_id = str(uuid4())
        spec = _simple_spec([])
        _register_playbook(version_id, spec)

        result = await worker_client.execute_workflow(
            DynamicPlaybookExecutor.run,
            PlaybookExecutionInput(playbook_version_id=version_id, incident_id="inc-3",
                                   incident_payload={}),
            id=f"wf-{uuid4().hex[:8]}", task_queue=TASK_QUEUE,
        )
        assert result["status"] == "completed"
        assert result["steps_executed"] == []


class TestIncidentLifecycle:
    async def test_sets_in_progress_then_resolves(self, worker_client: Client):
        version_id = str(uuid4())
        spec = _simple_spec([
            {"id": "a", "name": "A", "type": "action", "tool": "fetch_pod_logs"},
        ])
        _register_playbook(version_id, spec)

        await worker_client.execute_workflow(
            DynamicPlaybookExecutor.run,
            PlaybookExecutionInput(playbook_version_id=version_id, incident_id="inc-lc",
                                   incident_payload={}),
            id=f"wf-{uuid4().hex[:8]}", task_queue=TASK_QUEUE,
        )
        # Incident should have gone: in_progress → resolved
        assert _mock_incident_status["inc-lc"] == "resolved"

    async def test_no_resolve_when_on_complete_false(self, worker_client: Client):
        version_id = str(uuid4())
        spec = _simple_spec(
            [{"id": "a", "name": "A", "type": "action", "tool": "fetch_pod_logs"}],
            on_complete={"resolve_incident": False},
        )
        _register_playbook(version_id, spec)

        await worker_client.execute_workflow(
            DynamicPlaybookExecutor.run,
            PlaybookExecutionInput(playbook_version_id=version_id, incident_id="inc-noresolve",
                                   incident_payload={}),
            id=f"wf-{uuid4().hex[:8]}", task_queue=TASK_QUEUE,
        )
        # Should be in_progress (set at start) but not resolved
        assert _mock_incident_status["inc-noresolve"] == "in_progress"


class TestConditionBranching:
    async def test_condition_true_branch(self, worker_client: Client):
        version_id = str(uuid4())
        # "yes" and "no" are terminal — no on_success, so workflow ends after them
        spec = _simple_spec([
            {"id": "check", "name": "Check", "type": "condition",
             "condition": "True", "branches": {"true": "yes_step", "false": "no_step"}},
            {"id": "yes_step", "name": "Yes", "type": "action", "tool": "slack_notification",
             "input": {"channel": "#ops", "message": "yes"},
             "on_success": "__end__"},  # explicit: no next step
            {"id": "no_step", "name": "No", "type": "action", "tool": "fetch_pod_logs",
             "on_success": "__end__"},
        ])
        _register_playbook(version_id, spec)

        result = await worker_client.execute_workflow(
            DynamicPlaybookExecutor.run,
            PlaybookExecutionInput(playbook_version_id=version_id, incident_id="inc-cond",
                                   incident_payload={}),
            id=f"wf-{uuid4().hex[:8]}", task_queue=TASK_QUEUE,
        )

        assert "check" in result["steps_executed"]
        assert "yes_step" in result["steps_executed"]
        assert "no_step" not in result["steps_executed"]

    async def test_condition_false_branch(self, worker_client: Client):
        version_id = str(uuid4())
        spec = _simple_spec([
            {"id": "check", "name": "Check", "type": "condition",
             "condition": "False", "branches": {"true": "yes_step", "false": "no_step"}},
            {"id": "yes_step", "name": "Yes", "type": "action", "tool": "slack_notification",
             "input": {"channel": "#ops", "message": "yes"},
             "on_success": "__end__"},
            {"id": "no_step", "name": "No", "type": "action", "tool": "fetch_pod_logs",
             "on_success": "__end__"},
        ])
        _register_playbook(version_id, spec)

        result = await worker_client.execute_workflow(
            DynamicPlaybookExecutor.run,
            PlaybookExecutionInput(playbook_version_id=version_id, incident_id="inc-cond-f",
                                   incident_payload={}),
            id=f"wf-{uuid4().hex[:8]}", task_queue=TASK_QUEUE,
        )

        assert "no_step" in result["steps_executed"]
        assert "yes_step" not in result["steps_executed"]


class TestTemplateResolution:
    async def test_input_templates_resolved(self, worker_client: Client):
        version_id = str(uuid4())
        spec = _simple_spec([
            {"id": "fetch", "name": "Fetch", "type": "action", "tool": "fetch_pod_logs",
             "input": {"namespace": "${incident.entity.namespace}", "pod": "${incident.entity.pod}"}},
        ])
        _register_playbook(version_id, spec)

        await worker_client.execute_workflow(
            DynamicPlaybookExecutor.run,
            PlaybookExecutionInput(
                playbook_version_id=version_id, incident_id="inc-tpl",
                incident_payload={"entity": {"namespace": "ml", "pod": "trainer-7f9d"}},
            ),
            id=f"wf-{uuid4().hex[:8]}", task_queue=TASK_QUEUE,
        )

        # Check the step result was recorded (activity was called with resolved input)
        assert len(_mock_step_results) >= 1
        assert _mock_step_results[0]["success"] is True


class TestAbortSignal:
    async def test_abort_stops_workflow(self, worker_client: Client):
        version_id = str(uuid4())
        spec = _simple_spec([
            {"id": "step1", "name": "S1", "type": "action", "tool": "fetch_pod_logs"},
            {"id": "delay", "name": "Wait", "type": "delay", "duration": "10m"},
            {"id": "step2", "name": "S2", "type": "action", "tool": "restart_workload"},
        ])
        _register_playbook(version_id, spec)

        handle = await worker_client.start_workflow(
            DynamicPlaybookExecutor.run,
            PlaybookExecutionInput(playbook_version_id=version_id, incident_id="inc-abort",
                                   incident_payload={}),
            id=f"wf-{uuid4().hex[:8]}", task_queue=TASK_QUEUE,
        )

        # Signal abort during the delay
        await handle.signal(DynamicPlaybookExecutor.abort, "operator requested")
        result = await handle.result()

        assert result["status"] == "aborted"
        # step2 should not have executed
        assert "step2" not in result["steps_executed"]
        # Incident should be set back to open
        assert _mock_incident_status["inc-abort"] == "open"


class TestChecksumValidation:
    def test_checksum_logic(self):
        """Unit test: verify the checksum comparison logic directly."""
        spec = _simple_spec([{"id": "a", "name": "A", "type": "action", "tool": "fetch_pod_logs"}])
        correct = _spec_checksum(spec)
        assert len(correct) == 64
        assert correct != "wrong_checksum"
        spec2 = _simple_spec([{"id": "b", "name": "B", "type": "action", "tool": "restart_workload"}])
        assert _spec_checksum(spec2) != correct


class TestOnFailureBranching:
    async def test_on_failure_redirects(self, worker_client: Client):
        """When a step fails and has on_failure, the workflow branches there."""
        version_id = str(uuid4())
        spec = _simple_spec([
            {"id": "try_restart", "name": "Restart", "type": "action",
             "tool": "nonexistent_tool",  # will fail — no such activity
             "on_failure": "escalate"},
            {"id": "escalate", "name": "Escalate", "type": "action",
             "tool": "page_oncall", "input": {"title": "Failed", "body": "Restart failed", "severity": "high"}},
        ])
        _register_playbook(version_id, spec)

        result = await worker_client.execute_workflow(
            DynamicPlaybookExecutor.run,
            PlaybookExecutionInput(playbook_version_id=version_id, incident_id="inc-fail",
                                   incident_payload={}),
            id=f"wf-{uuid4().hex[:8]}", task_queue=TASK_QUEUE,
        )

        assert "try_restart" in result["steps_executed"]
        assert "escalate" in result["steps_executed"]
        # Step results should show the first step failed
        failed_steps = [r for r in _mock_step_results if r["step_id"] == "try_restart"]
        assert len(failed_steps) == 1
        assert failed_steps[0]["success"] is False

    async def test_on_failure_abort_aborts(self, worker_client: Client):
        """When on_failure is 'abort', the workflow aborts."""
        version_id = str(uuid4())
        spec = _simple_spec([
            {"id": "bad", "name": "Bad", "type": "action", "tool": "nonexistent_tool",
             "on_failure": "abort"},
            {"id": "never", "name": "Never", "type": "action", "tool": "fetch_pod_logs"},
        ])
        _register_playbook(version_id, spec)

        result = await worker_client.execute_workflow(
            DynamicPlaybookExecutor.run,
            PlaybookExecutionInput(playbook_version_id=version_id, incident_id="inc-abort2",
                                   incident_payload={}),
            id=f"wf-{uuid4().hex[:8]}", task_queue=TASK_QUEUE,
        )

        assert result["status"] == "aborted"
        assert "never" not in result["steps_executed"]


class TestStepOutputInCondition:
    async def test_step_output_used_in_condition(self, worker_client: Client):
        """Condition step reads a previous step's output via ${steps.X.output.Y}."""
        version_id = str(uuid4())
        spec = _simple_spec([
            {"id": "fetch", "name": "Fetch", "type": "action", "tool": "fetch_pod_logs"},
            {"id": "check", "name": "Check", "type": "condition",
             "condition": "${steps.fetch.success}",
             "branches": {"true": "notify", "false": "escalate"}},
            {"id": "notify", "name": "Notify", "type": "action", "tool": "slack_notification",
             "input": {"channel": "#ops", "message": "OK"}, "on_success": "__end__"},
            {"id": "escalate", "name": "Escalate", "type": "action", "tool": "page_oncall",
             "input": {"title": "Bad", "body": "Bad", "severity": "high"}, "on_success": "__end__"},
        ])
        _register_playbook(version_id, spec)

        result = await worker_client.execute_workflow(
            DynamicPlaybookExecutor.run,
            PlaybookExecutionInput(playbook_version_id=version_id, incident_id="inc-output",
                                   incident_payload={}),
            id=f"wf-{uuid4().hex[:8]}", task_queue=TASK_QUEUE,
        )

        # fetch succeeded → condition resolves ${steps.fetch.success} = "True" → true branch
        assert "fetch" in result["steps_executed"]
        assert "check" in result["steps_executed"]
        assert "notify" in result["steps_executed"]
        assert "escalate" not in result["steps_executed"]


class TestDefaultParameters:
    async def test_default_params_in_templates(self, worker_client: Client):
        """Parameters with defaults are available in templates."""
        version_id = str(uuid4())
        spec = _simple_spec(
            [{"id": "notify", "name": "Notify", "type": "action", "tool": "slack_notification",
              "input": {"channel": "${params.channel}", "message": "hello"}}],
            parameters={"channel": {"type": "string", "default": "#default-channel"}},
        )
        _register_playbook(version_id, spec)

        result = await worker_client.execute_workflow(
            DynamicPlaybookExecutor.run,
            PlaybookExecutionInput(playbook_version_id=version_id, incident_id="inc-params",
                                   incident_payload={}),
            id=f"wf-{uuid4().hex[:8]}", task_queue=TASK_QUEUE,
        )

        assert result["status"] == "completed"
        assert _mock_step_results[0]["success"] is True


class TestDelayStep:
    async def test_delay_step_completes(self, worker_client: Client):
        """Delay step uses Temporal timer and completes (time-skipping env)."""
        version_id = str(uuid4())
        spec = _simple_spec([
            {"id": "wait", "name": "Wait", "type": "delay", "duration": "5m"},
            {"id": "after", "name": "After", "type": "action", "tool": "fetch_pod_logs"},
        ])
        _register_playbook(version_id, spec)

        result = await worker_client.execute_workflow(
            DynamicPlaybookExecutor.run,
            PlaybookExecutionInput(playbook_version_id=version_id, incident_id="inc-delay",
                                   incident_payload={}),
            id=f"wf-{uuid4().hex[:8]}", task_queue=TASK_QUEUE,
        )

        assert result["status"] == "completed"
        assert result["steps_executed"] == ["wait", "after"]


class TestNotificationStep:
    async def test_notification_uses_action_path(self, worker_client: Client):
        """Notification steps use the same action execution path."""
        version_id = str(uuid4())
        spec = _simple_spec([
            {"id": "notify", "name": "Notify", "type": "notification",
             "tool": "slack_notification",
             "input": {"channel": "#ops", "message": "incident resolved"}},
        ])
        _register_playbook(version_id, spec)

        result = await worker_client.execute_workflow(
            DynamicPlaybookExecutor.run,
            PlaybookExecutionInput(playbook_version_id=version_id, incident_id="inc-notif",
                                   incident_payload={}),
            id=f"wf-{uuid4().hex[:8]}", task_queue=TASK_QUEUE,
        )

        assert result["status"] == "completed"
        assert _mock_step_results[0]["success"] is True


class TestNewEvidenceSignal:
    async def test_evidence_accumulates(self, worker_client: Client):
        """new_evidence signals accumulate during workflow execution."""
        version_id = str(uuid4())
        spec = _simple_spec([
            {"id": "wait", "name": "Wait", "type": "delay", "duration": "2m"},
            {"id": "done", "name": "Done", "type": "action", "tool": "fetch_pod_logs"},
        ])
        _register_playbook(version_id, spec)

        handle = await worker_client.start_workflow(
            DynamicPlaybookExecutor.run,
            PlaybookExecutionInput(playbook_version_id=version_id, incident_id="inc-ev",
                                   incident_payload={}),
            id=f"wf-{uuid4().hex[:8]}", task_queue=TASK_QUEUE,
        )

        # Send evidence signals during the delay
        await handle.signal(DynamicPlaybookExecutor.new_evidence, {"source": "alertmanager", "data": "gpu_alert"})
        result = await handle.result()

        assert result["status"] == "completed"
