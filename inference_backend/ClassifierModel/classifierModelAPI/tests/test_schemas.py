"""Schema validation tests for AnomalyRequest / AnomalyResponse."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.anomaly import LABEL_NAMES, AnomalyRequest, AnomalyResponse


class TestAnomalyRequest:
    def test_minimal_valid_payload(self):
        req = AnomalyRequest(logs=[{"body": "hello"}])
        assert req.logs == [{"body": "hello"}]
        assert req.max_logs == 200
        assert req.entity_context == {}

    def test_full_core_shape(self):
        """Exact shape the core backend's WindowWorker emits."""
        req = AnomalyRequest(
            entity_key="cluster/ns/workload",
            window_start="2026-04-14T12:00:00Z",
            window_end="2026-04-14T12:05:00Z",
            logs=[{"body": "pod crashed", "attributes": {"namespace": "prod"}}],
            max_logs=200,
            entity_context={"namespace": "prod", "workload": "reco"},
        )
        assert req.entity_key == "cluster/ns/workload"
        assert req.max_logs == 200

    def test_empty_logs_rejected(self):
        with pytest.raises(ValidationError) as exc:
            AnomalyRequest(logs=[])
        assert "at least one entry" in str(exc.value)

    def test_missing_logs_rejected(self):
        with pytest.raises(ValidationError):
            AnomalyRequest()  # type: ignore[call-arg]

    def test_extra_fields_ignored(self):
        """Tolerance for caller-side schema drift."""
        req = AnomalyRequest(
            logs=[{"body": "x"}],
            sequence_ids=[1, 2, 3],  # legacy field — should not blow up
            future_field="whatever",
        )
        assert req.logs == [{"body": "x"}]

    def test_max_logs_must_be_positive(self):
        with pytest.raises(ValidationError):
            AnomalyRequest(logs=[{"body": "x"}], max_logs=0)


class TestAnomalyResponse:
    def test_valid(self):
        resp = AnomalyResponse(class_id=1, confidence_score=0.87, label="Resource_Exhaustion")
        assert resp.class_id == 1

    def test_class_id_range(self):
        with pytest.raises(ValidationError):
            AnomalyResponse(class_id=7, confidence_score=0.5, label="x")
        with pytest.raises(ValidationError):
            AnomalyResponse(class_id=-1, confidence_score=0.5, label="x")

    def test_confidence_range(self):
        with pytest.raises(ValidationError):
            AnomalyResponse(class_id=0, confidence_score=1.5, label="Normal")


class TestLabelNames:
    def test_all_seven_classes(self):
        assert set(LABEL_NAMES.keys()) == {0, 1, 2, 3, 4, 5, 6}

    def test_names_unique(self):
        assert len(set(LABEL_NAMES.values())) == 7
