"""Tests for all Pydantic domain models.

Covers construction, validation, serialization, enums, key builders,
defaults, and JSON round-trips.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# incidents.py
# ---------------------------------------------------------------------------
from app.domain.incidents import (
    CanonicalIncident,
    ClassifierEvidence,
    EntityInfo,
    IncidentEvidence,
    IncidentStatus,
    Severity,
)


class TestIncidentEnums:
    def test_incident_status_values(self):
        assert set(IncidentStatus) == {
            IncidentStatus.OPEN,
            IncidentStatus.ACKNOWLEDGED,
            IncidentStatus.IN_PROGRESS,
            IncidentStatus.RESOLVED,
            IncidentStatus.CLOSED,
            IncidentStatus.SUPPRESSED,
        }

    def test_severity_values(self):
        assert set(Severity) == {
            Severity.CRITICAL,
            Severity.HIGH,
            Severity.MEDIUM,
            Severity.LOW,
            Severity.INFO,
        }

    def test_status_string_values(self):
        assert IncidentStatus.IN_PROGRESS == "in_progress"
        assert Severity.CRITICAL == "critical"


class TestEntityInfo:
    def test_all_optional(self):
        e = EntityInfo()
        assert e.cluster is None

    def test_partial_fields(self):
        e = EntityInfo(cluster="prod-a", namespace="ml", gpu_id="2")
        assert e.cluster == "prod-a"
        assert e.gpu_id == "2"
        assert e.pod is None


class TestIncidentEvidence:
    def test_defaults(self):
        ev = IncidentEvidence()
        assert ev.metric_alerts == []
        assert ev.classifier is None
        assert ev.raw_signals == []

    def test_with_classifier(self):
        c = ClassifierEvidence(label="failure.memory", confidence=0.94)
        ev = IncidentEvidence(classifier=c, metric_alerts=["GPUHighMemoryPressure"])
        assert ev.classifier.label == "failure.memory"
        assert ev.metric_alerts == ["GPUHighMemoryPressure"]


class TestCanonicalIncident:
    def _make(self, **overrides):
        defaults = dict(
            incident_key="prod-a/ml/trainer/failure.memory",
            incident_type="incident.gpu_memory_failure",
            entity=EntityInfo(cluster="prod-a", namespace="ml", service="trainer"),
            entity_key="prod-a/ml/trainer",
            sources=["log_classifier"],
            evidence=IncidentEvidence(),
        )
        defaults.update(overrides)
        return CanonicalIncident(**defaults)

    def test_defaults(self):
        inc = self._make()
        assert inc.status == IncidentStatus.OPEN
        assert inc.severity == Severity.MEDIUM
        assert isinstance(inc.id, UUID)
        assert isinstance(inc.created_at, datetime)

    def test_json_round_trip(self):
        inc = self._make()
        data = json.loads(inc.model_dump_json())
        restored = CanonicalIncident.model_validate(data)
        assert restored.incident_key == inc.incident_key
        assert restored.status == inc.status

    def test_custom_fields(self):
        inc = self._make(
            status=IncidentStatus.IN_PROGRESS,
            severity=Severity.CRITICAL,
            temporal_workflow_id="wf-123",
        )
        assert inc.status == IncidentStatus.IN_PROGRESS
        assert inc.severity == Severity.CRITICAL
        assert inc.temporal_workflow_id == "wf-123"


# ---------------------------------------------------------------------------
# keys.py
# ---------------------------------------------------------------------------
from app.domain.keys import (
    DEFAULT_KEY_TEMPLATE,
    SUPPORTED_KEY_TEMPLATES,
    build_entity_key,
    build_incident_key,
)


class TestBuildEntityKey:
    def test_default_template(self):
        attrs = {"cluster": "prod-a", "namespace": "ml", "service": "trainer"}
        assert build_entity_key(attrs) == "prod-a/ml/trainer"

    def test_custom_template(self):
        attrs = {"cluster": "prod-a", "namespace": "ml", "pod": "trainer-7f9d"}
        key = build_entity_key(attrs, "{cluster}/{namespace}/{pod}")
        assert key == "prod-a/ml/trainer-7f9d"

    def test_fallback_on_missing_fields(self):
        attrs = {"cluster": "prod-a", "pod": "trainer-7f9d"}
        key = build_entity_key(attrs)  # default template needs namespace + service
        assert key == "prod-a/trainer-7f9d"

    def test_empty_attributes(self):
        assert build_entity_key({}) == "unknown"

    def test_default_template_value(self):
        assert DEFAULT_KEY_TEMPLATE == "{cluster}/{namespace}/{service}"

    def test_supported_templates_count(self):
        assert len(SUPPORTED_KEY_TEMPLATES) == 5


class TestBuildIncidentKey:
    def test_basic(self):
        assert build_incident_key("prod-a/ml/trainer", "failure.memory") == \
            "prod-a/ml/trainer/failure.memory"


# ---------------------------------------------------------------------------
# events.py
# ---------------------------------------------------------------------------
from app.domain.events import (
    ClassifiedLogEvent,
    ClassificationInfo,
    ClassifierInput,
    ClassifierOutput,
    InternalSignal,
    LogEntry,
    SecondaryLabel,
    SignalType,
    WindowInfo,
)


class TestClassifierIO:
    def test_classifier_input(self):
        inp = ClassifierInput(
            entity_key="prod-a/ml/trainer",
            window_start="2025-01-15T10:25:00Z",
            window_end="2025-01-15T10:30:00Z",
            logs=[LogEntry(timestamp="2025-01-15T10:27:12Z", body="CUDA error: out of memory")],
        )
        assert inp.max_logs == 200
        assert len(inp.logs) == 1

    def test_classifier_output(self):
        out = ClassifierOutput(
            label="failure.memory",
            confidence=0.94,
            evidence=["CUDA error: out of memory"],
            severity_suggestion="high",
            secondary_labels=[SecondaryLabel(label="failure.gpu", confidence=0.82)],
        )
        assert out.label == "failure.memory"
        assert len(out.secondary_labels) == 1
        assert out.secondary_labels[0].confidence == 0.82

    def test_classifier_output_defaults(self):
        out = ClassifierOutput(label="normal", confidence=0.99, evidence=[])
        assert out.severity_suggestion is None
        assert out.secondary_labels == []


class TestClassifiedLogEvent:
    def test_construction(self):
        evt = ClassifiedLogEvent(
            entity_key="prod-a/ml/trainer",
            entity=EntityInfo(cluster="prod-a", namespace="ml", service="trainer"),
            classification=ClassificationInfo(
                label="failure.memory", confidence=0.94, evidence=["CUDA OOM"]
            ),
            window=WindowInfo(start="2025-01-15T10:25:00Z", end="2025-01-15T10:30:00Z", log_count=47),
            timestamp=datetime(2025, 1, 15, 10, 30, 1, tzinfo=timezone.utc),
        )
        assert evt.event_type == "classified_log_event"
        assert isinstance(evt.event_id, UUID)

    def test_json_round_trip(self):
        evt = ClassifiedLogEvent(
            entity_key="prod-a/ml/trainer",
            entity=EntityInfo(cluster="prod-a"),
            classification=ClassificationInfo(label="normal", confidence=0.99, evidence=[]),
            window=WindowInfo(start="t0", end="t1", log_count=10),
            timestamp=datetime(2025, 1, 15, tzinfo=timezone.utc),
        )
        data = json.loads(evt.model_dump_json())
        restored = ClassifiedLogEvent.model_validate(data)
        assert restored.classification.label == "normal"


class TestInternalSignal:
    def test_construction(self):
        sig = InternalSignal(
            signal_type=SignalType.PROMETHEUS_ALERT,
            source="alertmanager",
            entity_key="prod-a/ml/trainer",
            entity=EntityInfo(cluster="prod-a"),
            incident_type_hint="incident.gpu_memory_failure",
            severity="high",
            timestamp=datetime.now(timezone.utc),
        )
        assert sig.signal_type == SignalType.PROMETHEUS_ALERT
        assert isinstance(sig.signal_id, UUID)

    def test_signal_type_values(self):
        assert set(SignalType) == {
            SignalType.CLASSIFIER_OUTPUT,
            SignalType.PROMETHEUS_ALERT,
            SignalType.APP_EVENT,
            SignalType.MANUAL_TRIGGER,
        }


# ---------------------------------------------------------------------------
# tools.py
# ---------------------------------------------------------------------------
from app.domain.tools import (
    SideEffectLevel,
    ToolCreate,
    ToolRead,
    ToolSearchResult,
    ToolUpdate,
)


class TestToolModels:
    def test_side_effect_levels(self):
        assert set(SideEffectLevel) == {
            SideEffectLevel.READ,
            SideEffectLevel.WRITE,
            SideEffectLevel.DESTRUCTIVE,
        }

    def test_tool_create_defaults(self):
        t = ToolCreate(
            name="fetch_pod_logs",
            display_name="Fetch Pod Logs",
            description="Fetches logs from a pod",
            category="kubernetes",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        assert t.side_effect_level == SideEffectLevel.READ
        assert t.required_approvals == 0
        assert t.environments_allowed == ["production", "staging", "development"]

    def test_tool_update_all_optional(self):
        t = ToolUpdate()
        assert t.display_name is None
        assert t.is_active is None

    def test_tool_read_from_attributes(self):
        """ToolRead should support from_attributes for ORM compatibility."""
        assert ToolRead.model_config.get("from_attributes") is True

    def test_tool_search_result(self):
        r = ToolSearchResult(
            id=uuid4(),
            name="restart_workload",
            description="Restarts a workload",
            relevance_score=0.92,
            input_schema={"type": "object"},
            side_effect_level=SideEffectLevel.WRITE,
        )
        assert r.relevance_score == 0.92


# ---------------------------------------------------------------------------
# playbooks.py
# ---------------------------------------------------------------------------
from app.domain.playbooks import (
    AbortAction,
    CompletionAction,
    PlaybookCreate,
    PlaybookRead,
    PlaybookSpec,
    PlaybookStep,
    PlaybookTrigger,
    PlaybookVersionCreate,
    PlaybookVersionRead,
    PlaybookVersionStatus,
    RetryConfig,
    StepBranches,
    StepType,
    VALID_STATUS_TRANSITIONS,
)


class TestPlaybookVersionStatus:
    def test_all_statuses(self):
        assert set(PlaybookVersionStatus) == {
            PlaybookVersionStatus.DRAFT,
            PlaybookVersionStatus.GENERATED,
            PlaybookVersionStatus.VALIDATED,
            PlaybookVersionStatus.APPROVED,
            PlaybookVersionStatus.PUBLISHED,
            PlaybookVersionStatus.ARCHIVED,
        }

    def test_valid_transitions_from_draft(self):
        assert PlaybookVersionStatus.GENERATED in VALID_STATUS_TRANSITIONS[PlaybookVersionStatus.DRAFT]
        assert PlaybookVersionStatus.VALIDATED in VALID_STATUS_TRANSITIONS[PlaybookVersionStatus.DRAFT]

    def test_archived_is_terminal(self):
        assert VALID_STATUS_TRANSITIONS[PlaybookVersionStatus.ARCHIVED] == []

    def test_published_can_only_archive(self):
        assert VALID_STATUS_TRANSITIONS[PlaybookVersionStatus.PUBLISHED] == [PlaybookVersionStatus.ARCHIVED]


class TestStepType:
    def test_all_types(self):
        assert set(StepType) == {
            StepType.ACTION,
            StepType.APPROVAL,
            StepType.CONDITION,
            StepType.DELAY,
            StepType.PARALLEL,
            StepType.NOTIFICATION,
            StepType.SUB_PLAYBOOK,
        }


class TestPlaybookStep:
    def test_action_step(self):
        s = PlaybookStep(
            id="fetch_logs",
            name="Fetch Pod Logs",
            type=StepType.ACTION,
            tool="fetch_pod_logs",
            input={"namespace": "${incident.entity.namespace}"},
            timeout="5m",
        )
        assert s.tool == "fetch_pod_logs"
        assert s.timeout == "5m"

    def test_condition_step(self):
        s = PlaybookStep(
            id="check_restart",
            name="Check if restart helped",
            type=StepType.CONDITION,
            condition="${steps.restart.output.success}",
            branches=StepBranches(true="notify_success", false="escalate"),
        )
        assert s.branches.true == "notify_success"

    def test_delay_step(self):
        s = PlaybookStep(id="wait_5m", name="Wait", type=StepType.DELAY, duration="5m")
        assert s.duration == "5m"

    def test_approval_step(self):
        s = PlaybookStep(
            id="get_approval",
            name="Get Approval",
            type=StepType.APPROVAL,
            approval_channel="#incident-ops",
            approval_timeout="30m",
        )
        assert s.approval_channel == "#incident-ops"

    def test_retry_config(self):
        r = RetryConfig(max_attempts=3, backoff="exponential", initial_interval="10s")
        assert r.max_attempts == 3
        assert r.backoff == "exponential"


class TestPlaybookSpec:
    def _make_spec(self, **overrides):
        defaults = dict(
            name="GPU Memory Failure Recovery",
            version="1.0.0",
            trigger=PlaybookTrigger(incident_types=["incident.gpu_memory_failure"]),
            steps=[
                PlaybookStep(
                    id="fetch_logs",
                    name="Fetch Logs",
                    type=StepType.ACTION,
                    tool="fetch_pod_logs",
                ),
            ],
        )
        defaults.update(overrides)
        return PlaybookSpec(**defaults)

    def test_minimal_spec(self):
        spec = self._make_spec()
        assert spec.name == "GPU Memory Failure Recovery"
        assert len(spec.steps) == 1

    def test_json_round_trip(self):
        spec = self._make_spec(
            on_complete=CompletionAction(resolve_incident=True),
            on_abort=AbortAction(escalate=True, page_oncall=True),
        )
        data = json.loads(spec.model_dump_json())
        restored = PlaybookSpec.model_validate(data)
        assert restored.on_complete.resolve_incident is True
        assert restored.on_abort.page_oncall is True

    def test_trigger_with_filters(self):
        t = PlaybookTrigger(
            incident_types=["incident.gpu_memory_failure"],
            severity_filter=["critical", "high"],
            entity_filter={"cluster": "prod-a"},
        )
        spec = self._make_spec(trigger=t)
        assert spec.trigger.severity_filter == ["critical", "high"]

    def test_spec_requires_name(self):
        with pytest.raises(ValidationError):
            PlaybookSpec(
                version="1.0.0",
                trigger=PlaybookTrigger(incident_types=["x"]),
                steps=[PlaybookStep(id="a", name="A", type=StepType.ACTION)],
            )

    def test_spec_allows_empty_steps(self):
        # Pydantic doesn't enforce JSON Schema minItems; validation happens at
        # the service layer via the full JSON Schema validator (§19).
        spec = PlaybookSpec(
            name="Test",
            version="1.0.0",
            trigger=PlaybookTrigger(incident_types=["x"]),
            steps=[],
        )
        assert spec.steps == []


class TestPlaybookCRUD:
    def test_playbook_create(self):
        p = PlaybookCreate(name="Test Playbook", owner_team="platform")
        assert p.description is None

    def test_playbook_version_create(self):
        v = PlaybookVersionCreate(
            workflow_spec={"name": "test", "version": "1.0.0", "trigger": {}, "steps": []},
            change_notes="Initial version",
        )
        assert v.trigger_bindings is None

    def test_playbook_read_from_attributes(self):
        assert PlaybookRead.model_config.get("from_attributes") is True

    def test_playbook_version_read_from_attributes(self):
        assert PlaybookVersionRead.model_config.get("from_attributes") is True


# ---------------------------------------------------------------------------
# rules.py
# ---------------------------------------------------------------------------
from app.domain.rules import (
    AlertRuleCreate,
    AlertRuleRead,
    AlertRuleType,
    AlertRuleUpdate,
    TriggerRuleCreate,
    TriggerRuleRead,
)


class TestAlertRuleModels:
    def test_rule_types(self):
        assert set(AlertRuleType) == {
            AlertRuleType.PROMETHEUS,
            AlertRuleType.CLASSIFIER_THRESHOLD,
            AlertRuleType.COMPOSITE,
        }

    def test_create_defaults(self):
        r = AlertRuleCreate(
            name="High Error Rate",
            rule_type=AlertRuleType.PROMETHEUS,
            rule_definition={"expr": "rate(errors[5m]) > 0.05"},
        )
        assert r.severity == "medium"
        assert r.is_active is True

    def test_update_all_optional(self):
        r = AlertRuleUpdate()
        assert r.name is None

    def test_read_from_attributes(self):
        assert AlertRuleRead.model_config.get("from_attributes") is True


class TestTriggerRuleModels:
    def test_create_defaults(self):
        r = TriggerRuleCreate(
            incident_type="incident.gpu_memory_failure",
            playbook_version_id=uuid4(),
        )
        assert r.priority == 0
        assert r.is_active is True

    def test_read_from_attributes(self):
        assert TriggerRuleRead.model_config.get("from_attributes") is True
