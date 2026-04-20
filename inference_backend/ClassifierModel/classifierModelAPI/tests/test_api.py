"""FastAPI route tests for /health and /predict_anomaly.

Uses the TestClient with model/tokenizer stubs installed on app.state —
this avoids downloading roberta-base from HuggingFace in CI.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import torch
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client(mock_model_factory, mock_tokenizer):
    """TestClient with a preloaded mock model.

    Instantiated without the `with` context manager so the FastAPI lifespan
    does NOT fire — that would otherwise download roberta-base from the Hub
    and overwrite our mocks. We install mock state directly on app.state.
    """
    app.state.model = mock_model_factory([0.0, 5.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    app.state.tokenizer = mock_tokenizer
    app.state.device = torch.device("cpu")
    app.state.model_loaded = True

    yield TestClient(app)

    app.state.model_loaded = False


class TestHealth:
    def test_health_reports_loaded(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["model_loaded"] is True


class TestPredictAnomaly:
    def test_predicts_resource_exhaustion(self, client):
        resp = client.post(
            "/predict_anomaly",
            json={
                "entity_key": "prod/ns/reco",
                "window_start": "2026-04-14T12:00:00Z",
                "window_end": "2026-04-14T12:05:00Z",
                "logs": [
                    {"body": "pod OOMKilled", "attributes": {}},
                    {"body": "memory limit exceeded", "attributes": {}},
                ],
                "max_logs": 200,
                "entity_context": {},
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["class_id"] == 1
        assert body["label"] == "Resource_Exhaustion"
        assert 0.0 <= body["confidence_score"] <= 1.0

    def test_minimal_payload_just_logs(self, client):
        """Only `logs` is required; other fields default."""
        resp = client.post("/predict_anomaly", json={"logs": [{"body": "anything"}]})
        assert resp.status_code == 200

    def test_empty_logs_422(self, client):
        resp = client.post("/predict_anomaly", json={"logs": []})
        assert resp.status_code == 422

    def test_missing_logs_422(self, client):
        resp = client.post("/predict_anomaly", json={})
        assert resp.status_code == 422

    def test_service_unavailable_when_not_loaded(self):
        """503 when the model isn't loaded yet.

        Instantiating TestClient without the `with` context manager skips the
        lifespan startup, so the real roberta-base model is never downloaded —
        which is exactly the state we want to simulate.
        """
        app.state.model_loaded = False
        c = TestClient(app)
        resp = c.post("/predict_anomaly", json={"logs": [{"body": "x"}]})
        assert resp.status_code == 503

    def test_legacy_sequence_ids_ignored(self, client):
        """Caller can still send sequence_ids from an old client — we ignore it."""
        resp = client.post(
            "/predict_anomaly",
            json={
                "logs": [{"body": "anything"}],
                "sequence_ids": [100, 200, 300],  # legacy, silently ignored
            },
        )
        assert resp.status_code == 200

    def test_inference_failure_returns_500(self, client):
        """If the model raises, the endpoint should return 500."""
        app.state.model = MagicMock(side_effect=RuntimeError("boom"))
        resp = client.post(
            "/predict_anomaly",
            json={"logs": [{"body": "some real log"}]},
        )
        assert resp.status_code == 500
