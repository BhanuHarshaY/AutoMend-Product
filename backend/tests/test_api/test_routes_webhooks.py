"""Tests for webhook routes: alertmanager + OTLP ingestion.

The webhook routes push to Redis streams. Tests that need Redis
require it on port 6380. The transform_alertmanager_alert function
is tested as a unit test (no infra needed).
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.api.routes_webhooks import (
    _extract_attributes,
    _normalize_log_record,
    transform_alertmanager_alert,
)


# ===================================================================
# Unit tests (no infra required)
# ===================================================================


class TestTransformAlertmanagerAlert:
    def _sample_alert(self, **overrides):
        alert = {
            "status": "firing",
            "labels": {
                "alertname": "GPUHighMemoryPressure",
                "severity": "high",
                "incident_type": "incident.gpu_memory_failure",
                "namespace": "ml",
                "service": "trainer",
            },
            "annotations": {
                "summary": "GPU memory usage above 95%",
                "entity_cluster": "prod-a",
            },
            "startsAt": "2025-01-15T10:30:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "http://prometheus:9090/graph?g0.expr=...",
        }
        alert.update(overrides)
        return alert

    def test_basic_transform(self):
        signal = transform_alertmanager_alert(self._sample_alert())
        assert signal["signal_type"] == "prometheus_alert"
        assert signal["source"] == "alertmanager"
        assert signal["incident_type_hint"] == "incident.gpu_memory_failure"
        assert signal["severity"] == "high"
        assert signal["entity_key"] == "prod-a/ml/trainer"
        assert signal["timestamp"] == "2025-01-15T10:30:00Z"

    def test_entity_from_labels(self):
        alert = self._sample_alert()
        alert["labels"]["pod"] = "trainer-7f9d"
        signal = transform_alertmanager_alert(alert)
        entity = json.loads(signal["entity"])
        assert entity["pod"] == "trainer-7f9d"
        assert entity["namespace"] == "ml"

    def test_entity_from_annotations_prefix(self):
        signal = transform_alertmanager_alert(self._sample_alert())
        entity = json.loads(signal["entity"])
        # "cluster" comes from annotations.entity_cluster
        assert entity["cluster"] == "prod-a"

    def test_payload_contains_alert_fields(self):
        signal = transform_alertmanager_alert(self._sample_alert())
        payload = json.loads(signal["payload"])
        assert payload["alert_name"] == "GPUHighMemoryPressure"
        assert payload["status"] == "firing"
        assert payload["summary"] == "GPU memory usage above 95%"

    def test_missing_incident_type_defaults(self):
        alert = self._sample_alert()
        del alert["labels"]["incident_type"]
        signal = transform_alertmanager_alert(alert)
        assert signal["incident_type_hint"] == "incident.unknown"

    def test_missing_severity_defaults(self):
        alert = self._sample_alert()
        del alert["labels"]["severity"]
        signal = transform_alertmanager_alert(alert)
        assert signal["severity"] == "medium"

    def test_signal_id_is_uuid(self):
        signal = transform_alertmanager_alert(self._sample_alert())
        from uuid import UUID
        UUID(signal["signal_id"])  # Raises if not valid UUID


class TestExtractAttributes:
    def test_string_value(self):
        attrs = [{"key": "service.name", "value": {"stringValue": "trainer"}}]
        result = _extract_attributes(attrs)
        assert result["service.name"] == "trainer"

    def test_int_value(self):
        attrs = [{"key": "count", "value": {"intValue": 42}}]
        result = _extract_attributes(attrs)
        assert result["count"] == "42"

    def test_empty_list(self):
        assert _extract_attributes([]) == {}

    def test_multiple_attrs(self):
        attrs = [
            {"key": "a", "value": {"stringValue": "1"}},
            {"key": "b", "value": {"boolValue": True}},
        ]
        result = _extract_attributes(attrs)
        assert result == {"a": "1", "b": "True"}


class TestNormalizeLogRecord:
    def test_basic_normalize(self):
        record = {
            "timeUnixNano": "1705312032000000000",
            "body": {"stringValue": "CUDA error: out of memory"},
            "severityText": "ERROR",
            "attributes": [
                {"key": "pod", "value": {"stringValue": "trainer-7f9d"}},
            ],
        }
        resource = {"namespace": "ml", "service": "trainer", "cluster": "prod-a"}
        result = _normalize_log_record(record, resource)
        assert result["body"] == "CUDA error: out of memory"
        assert result["severity"] == "ERROR"
        assert result["entity_key"] == "prod-a/ml/trainer"
        assert "pod" in json.loads(result["attributes"])


# ===================================================================
# Integration tests (require Redis + Postgres for full app)
# ===================================================================

try:
    import psycopg2
    import socket
    from passlib.context import CryptContext
    from fastapi.testclient import TestClient

    from main_api import create_app

    _HAS_PG = True
except ImportError:
    _HAS_PG = False


def _pg_available() -> bool:
    if not _HAS_PG:
        return False
    try:
        conn = psycopg2.connect(
            dbname="automend", user="automend", password="automend",
            host="localhost", port=5432, connect_timeout=3,
        )
        conn.close()
        return True
    except Exception:
        return False


def _redis_available() -> bool:
    """Check that a real Redis (not Ray) is responding on port 6379."""
    try:
        import redis
        r = redis.Redis(host="localhost", port=6379, socket_connect_timeout=2)
        r.ping()
        r.close()
        return True
    except Exception:
        return False


_infra_up = _pg_available() and _redis_available()


class TestAlertmanagerWebhookIntegration:
    @pytest.mark.skipif(not _infra_up, reason="Postgres+Redis not available")
    def test_alertmanager_webhook_processes_alerts(self):
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            payload = {
                "alerts": [
                    {
                        "status": "firing",
                        "labels": {
                            "alertname": "GPUHighMemoryPressure",
                            "severity": "high",
                            "incident_type": "incident.gpu_memory_failure",
                            "namespace": "ml",
                        },
                        "annotations": {"summary": "GPU OOM"},
                        "startsAt": "2025-01-15T10:30:00Z",
                    },
                    {
                        "status": "firing",
                        "labels": {"alertname": "PodCrashLooping", "severity": "medium"},
                        "annotations": {},
                        "startsAt": "2025-01-15T10:31:00Z",
                    },
                ],
            }
            resp = client.post("/api/webhooks/alertmanager", json=payload)
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok", "processed": 2}
