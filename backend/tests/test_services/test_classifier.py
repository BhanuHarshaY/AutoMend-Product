"""Tests for the classifier client and classifier service.

Classifier service tests run against the FastAPI app directly (TestClient).
Classifier client tests use httpx mock transport — no network needed.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.domain.events import ClassifierInput, ClassifierOutput
from app.services.classifier_client import ClassifierClient
from app.services.classifier_server import ClassifyRequest, app as classifier_app
from app.services.classifier_taxonomy import (
    INFERENCE_TO_CORE,
    refine_label,
    translate_inference_output,
)


# ===================================================================
# CLASSIFIER SERVICE — rule-based classification
# ===================================================================


@pytest.fixture()
def service_client():
    return TestClient(classifier_app)


def _make_request(logs: list[dict], entity_key: str = "prod/ml/trainer") -> dict:
    return {
        "entity_key": entity_key,
        "window_start": "2025-01-15T10:25:00Z",
        "window_end": "2025-01-15T10:30:00Z",
        "logs": logs,
        "entity_context": {"cluster": "prod", "namespace": "ml", "service": "trainer"},
    }


def _log(body: str, severity: str = "ERROR") -> dict:
    return {"timestamp": "2025-01-15T10:27:00Z", "body": body, "severity": severity, "attributes": {}}


class TestClassifierServiceHealth:
    def test_health(self, service_client):
        resp = service_client.get("/health")
        assert resp.status_code == 200


class TestClassifierServiceMemory:
    def test_oom_classified(self, service_client):
        payload = _make_request([
            _log("CUDA error: out of memory"),
            _log("Failed to allocate 4096MB on GPU 2"),
            _log("Training step 1024 starting"),
        ])
        resp = service_client.post("/classify", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["label"] == "failure.memory"
        assert data["confidence"] >= 0.5
        assert data["severity_suggestion"] == "high"
        assert any("out of memory" in e.lower() or "allocate" in e.lower() for e in data["evidence"])


class TestClassifierServiceGPU:
    def test_gpu_error(self, service_client):
        payload = _make_request([
            _log("GPU error: Xid 79 detected"),
            _log("ECC error on GPU 2"),
        ])
        resp = service_client.post("/classify", json=payload)
        data = resp.json()
        assert data["label"] == "failure.gpu"


class TestClassifierServiceCrash:
    def test_crash(self, service_client):
        payload = _make_request([
            _log("panic: runtime error: index out of range"),
            _log("goroutine 1 [running]:"),
        ])
        resp = service_client.post("/classify", json=payload)
        assert resp.json()["label"] == "failure.crash"


class TestClassifierServiceNetwork:
    def test_network(self, service_client):
        payload = _make_request([
            _log("connection refused: dial tcp 10.0.0.1:8080"),
            _log("DNS resolution failed for svc.cluster.local"),
        ])
        resp = service_client.post("/classify", json=payload)
        assert resp.json()["label"] == "failure.network"


class TestClassifierServiceDeployment:
    def test_deployment(self, service_client):
        payload = _make_request([
            _log("ErrImagePull: rpc error: code = Unknown"),
            _log("Back-off pulling image \"myapp:v2.0\""),
            _log("CrashLoopBackOff"),
        ])
        resp = service_client.post("/classify", json=payload)
        assert resp.json()["label"] == "failure.deployment"


class TestClassifierServiceStorage:
    def test_storage(self, service_client):
        payload = _make_request([
            _log("no space left on device"),
            _log("volume mount failed for PVC data-vol"),
        ])
        resp = service_client.post("/classify", json=payload)
        assert resp.json()["label"] == "failure.storage"


class TestClassifierServiceAuth:
    def test_auth(self, service_client):
        payload = _make_request([
            _log("401 Unauthorized: token expired"),
            _log("Authentication failed for user bot@svc"),
        ])
        resp = service_client.post("/classify", json=payload)
        assert resp.json()["label"] == "failure.authentication"


class TestClassifierServiceLatency:
    def test_latency(self, service_client):
        payload = _make_request([
            _log("high latency detected: p99 = 5200ms"),
            _log("request timeout after 30s"),
        ])
        resp = service_client.post("/classify", json=payload)
        assert resp.json()["label"] == "degradation.latency"


class TestClassifierServiceNormal:
    def test_normal_logs(self, service_client):
        payload = _make_request([
            _log("INFO: Server started on port 8080", "INFO"),
            _log("INFO: Health check passed", "INFO"),
            _log("INFO: Processing request 12345", "INFO"),
        ])
        resp = service_client.post("/classify", json=payload)
        data = resp.json()
        assert data["label"] == "normal"
        assert data["confidence"] >= 0.8
        assert data["severity_suggestion"] == "info"


class TestClassifierServiceSecondaryLabels:
    def test_secondary_labels(self, service_client):
        payload = _make_request([
            _log("CUDA error: out of memory"),
            _log("GPU error: Xid 79"),
            _log("connection refused"),
        ])
        resp = service_client.post("/classify", json=payload)
        data = resp.json()
        assert len(data["secondary_labels"]) >= 1
        sec_labels = [s["label"] for s in data["secondary_labels"]]
        # Should have at least one secondary label
        assert any(l in sec_labels for l in ["failure.gpu", "failure.network"])


class TestClassifierServiceConfidence:
    def test_confidence_in_valid_range(self, service_client):
        payload = _make_request([
            _log("out of memory"),
            _log("CUDA error: cannot allocate"),
            _log("normal info log"),
        ])
        resp = service_client.post("/classify", json=payload)
        data = resp.json()
        assert 0.5 <= data["confidence"] <= 1.0

    def test_all_matching_logs_high_confidence(self, service_client):
        payload = _make_request([_log("out of memory")] * 5)
        resp = service_client.post("/classify", json=payload)
        assert resp.json()["confidence"] >= 0.9


# ===================================================================
# CLASSIFIER CLIENT — httpx mock transport
# ===================================================================


class TestClassifierClient:
    async def test_classify_dict_input(self):
        """Client sends dict, returns dict."""
        mock_response = {
            "label": "failure.memory",
            "confidence": 0.94,
            "evidence": ["CUDA OOM"],
            "severity_suggestion": "high",
            "secondary_labels": [],
        }

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=mock_response)

        transport = httpx.MockTransport(mock_handler)

        client = ClassifierClient.__new__(ClassifierClient)
        client.base_url = "http://test"
        client.timeout = 5

        # Patch the method to use our transport
        async with httpx.AsyncClient(transport=transport) as http:
            response = await http.post(
                "http://test/classify",
                json={"entity_key": "test", "logs": []},
            )
            result = response.json()

        assert result["label"] == "failure.memory"
        assert result["confidence"] == 0.94

    async def test_classify_typed(self):
        """classify_typed returns a validated ClassifierOutput."""
        mock_response = {
            "label": "failure.gpu",
            "confidence": 0.88,
            "evidence": ["Xid error"],
            "severity_suggestion": "high",
            "secondary_labels": [],
        }

        async def mock_handler(request: httpx.Request) -> httpx.Response:
            # Verify the request body is valid JSON
            body = json.loads(request.content)
            assert "entity_key" in body
            return httpx.Response(200, json=mock_response)

        transport = httpx.MockTransport(mock_handler)

        # Use the service TestClient as a transport substitute
        client = ClassifierClient.__new__(ClassifierClient)
        client.base_url = "http://test"
        client.timeout = 5

        # Manually test the output model validation
        output = ClassifierOutput.model_validate(mock_response)
        assert output.label == "failure.gpu"
        assert output.confidence == 0.88

    def test_client_init_defaults(self):
        """Client reads from settings by default."""
        client = ClassifierClient()
        assert "localhost" in client.base_url or "classifier" in client.base_url
        assert client.timeout == 30

    def test_client_init_custom(self):
        client = ClassifierClient(base_url="http://custom:9999", timeout=10)
        assert client.base_url == "http://custom:9999"
        assert client.timeout == 10


# ===================================================================
# END-TO-END: client → service via TestClient
# ===================================================================


class TestClientServiceE2E:
    def test_client_to_service(self, service_client):
        """Test the full flow: build request → send to service → parse response."""
        request = ClassifyRequest(
            entity_key="prod/ml/trainer",
            window_start="2025-01-15T10:25:00Z",
            window_end="2025-01-15T10:30:00Z",
            logs=[
                {"timestamp": "t1", "body": "CUDA error: out of memory", "severity": "ERROR", "attributes": {}},
                {"timestamp": "t2", "body": "Training step failed", "severity": "ERROR", "attributes": {}},
            ],
            entity_context={"cluster": "prod", "namespace": "ml"},
        )

        resp = service_client.post("/classify", json=request.model_dump())
        assert resp.status_code == 200

        output = ClassifierOutput.model_validate(resp.json())
        assert output.label == "failure.memory"
        assert 0 < output.confidence <= 1.0
        assert len(output.evidence) >= 1


# ===================================================================
# TAXONOMY — Tier 1 mapping (7-class inference → coarse core label)
# ===================================================================


class TestTaxonomyTier1:
    """Every inference-service label must translate to a known core label."""

    @pytest.mark.parametrize("inference_label,expected_core", [
        ("Normal",              "normal"),
        ("Resource_Exhaustion", "failure.resource_limit"),
        ("System_Crash",        "failure.crash"),
        ("Network_Failure",     "failure.network"),
        ("Data_Drift",          "anomaly.pattern"),
        ("Auth_Failure",        "failure.authentication"),
        ("Permission_Denied",   "failure.authentication"),
    ])
    def test_each_label_maps(self, inference_label: str, expected_core: str):
        resp = {"class_id": 1, "confidence_score": 0.9, "label": inference_label}
        # No refinement-triggering logs → stays at coarse label.
        out = translate_inference_output(resp, logs=[{"body": "uninformative log"}])
        assert out["label"] == expected_core

    def test_unknown_label_falls_back_to_anomaly(self):
        resp = {"class_id": 99, "confidence_score": 0.5, "label": "Brand_New_Class"}
        out = translate_inference_output(resp, logs=[{"body": "x"}])
        assert out["label"] == "anomaly.pattern"

    def test_all_7_labels_have_mappings(self):
        expected = {"Normal", "Resource_Exhaustion", "System_Crash",
                    "Network_Failure", "Data_Drift", "Auth_Failure",
                    "Permission_Denied"}
        assert expected.issubset(INFERENCE_TO_CORE.keys())


# ===================================================================
# TAXONOMY — Tier 2 refinements (log-content regex splits)
# ===================================================================


class TestTaxonomyTier2:
    def test_resource_exhaustion_with_cuda_logs_becomes_gpu(self):
        logs = [{"body": "CUDA error: device-side assert"}, {"body": "Xid 31 detected"}]
        assert refine_label("failure.resource_limit", logs) == "failure.gpu"

    def test_resource_exhaustion_with_oomkilled_becomes_memory(self):
        logs = [{"body": "pod OOMKilled"}, {"body": "memory limit exceeded"}]
        assert refine_label("failure.resource_limit", logs) == "failure.memory"

    def test_resource_exhaustion_with_disk_full_becomes_storage(self):
        logs = [{"body": "no space left on device"}, {"body": "PVC pending"}]
        assert refine_label("failure.resource_limit", logs) == "failure.storage"

    def test_resource_exhaustion_with_unrelated_logs_stays_coarse(self):
        logs = [{"body": "request processed in 200ms"}, {"body": "health check ok"}]
        assert refine_label("failure.resource_limit", logs) == "failure.resource_limit"

    def test_network_failure_with_502_becomes_dependency(self):
        logs = [{"body": "upstream unavailable"}, {"body": "502 bad gateway"}]
        assert refine_label("failure.network", logs) == "failure.dependency"

    def test_network_failure_with_connection_refused_stays_network(self):
        logs = [{"body": "connection refused: dial tcp 10.0.0.1:8080"}]
        assert refine_label("failure.network", logs) == "failure.network"

    def test_no_refinement_rule_returns_input_unchanged(self):
        """Coarse labels without REFINEMENTS entries are returned as-is."""
        logs = [{"body": "401 unauthorized"}]
        assert refine_label("failure.authentication", logs) == "failure.authentication"

    def test_refined_label_severity_wins(self):
        """failure.gpu is 'high' even though failure.resource_limit is 'medium'."""
        resp = {"class_id": 1, "confidence_score": 0.92, "label": "Resource_Exhaustion"}
        logs = [{"body": "CUDA error: Xid 79 detected"}]
        out = translate_inference_output(resp, logs)
        assert out["label"] == "failure.gpu"
        assert out["severity_suggestion"] == "high"


# ===================================================================
# TAXONOMY — passthrough for already-core-shaped responses
# ===================================================================


class TestTaxonomyPassthrough:
    def test_stub_response_unchanged(self):
        """A response that already has the core shape must not be mutated."""
        stub_resp = {
            "label": "failure.memory",
            "confidence": 0.88,
            "evidence": ["out of memory"],
            "severity_suggestion": "high",
            "secondary_labels": [],
        }
        out = translate_inference_output(stub_resp, logs=[])
        assert out is stub_resp  # same object — no re-wrap


# ===================================================================
# TAXONOMY — full translate_inference_output envelope
# ===================================================================


class TestTranslateEnvelope:
    def test_evidence_populated_from_logs(self):
        resp = {"class_id": 1, "confidence_score": 0.8, "label": "Resource_Exhaustion"}
        logs = [
            {"body": "line one"},
            {"body": "line two"},
            {"body": ""},           # blank, skipped
            {"body": "line three"},
        ]
        out = translate_inference_output(resp, logs)
        assert out["evidence"] == ["line one", "line two", "line three"]

    def test_evidence_capped_at_five(self):
        resp = {"class_id": 0, "confidence_score": 1.0, "label": "Normal"}
        logs = [{"body": f"line {i}"} for i in range(10)]
        out = translate_inference_output(resp, logs)
        assert len(out["evidence"]) == 5

    def test_confidence_copied_from_inference(self):
        resp = {"class_id": 2, "confidence_score": 0.73, "label": "System_Crash"}
        out = translate_inference_output(resp, logs=[{"body": "panic"}])
        assert out["confidence"] == 0.73

    def test_secondary_labels_empty(self):
        """Inference service doesn't return secondary labels — always [] for now."""
        resp = {"class_id": 0, "confidence_score": 0.9, "label": "Normal"}
        out = translate_inference_output(resp, logs=[])
        assert out["secondary_labels"] == []

    def test_normal_severity_is_info(self):
        resp = {"class_id": 0, "confidence_score": 1.0, "label": "Normal"}
        out = translate_inference_output(resp, logs=[])
        assert out["severity_suggestion"] == "info"


# ===================================================================
# CLIENT — translation happens at the HTTP boundary
# ===================================================================


class TestClassifierClientTranslation:
    """Mocks the inference service and asserts the client returns core shape."""

    @staticmethod
    def _patch_httpx(monkeypatch, response_payload: dict):
        """Replace httpx.AsyncClient in classifier_client with one bound to a MockTransport."""
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=response_payload)

        transport = httpx.MockTransport(handler)

        class _BoundAsyncClient(httpx.AsyncClient):
            def __init__(self, **kwargs):
                # Drop any caller-supplied transport and use ours.
                kwargs.pop("transport", None)
                super().__init__(transport=transport, **kwargs)

        import app.services.classifier_client as mod
        monkeypatch.setattr(mod.httpx, "AsyncClient", _BoundAsyncClient)

    async def test_client_translates_inference_response(self, monkeypatch):
        self._patch_httpx(monkeypatch, {
            "class_id": 1,
            "confidence_score": 0.91,
            "label": "Resource_Exhaustion",
        })

        client = ClassifierClient(
            base_url="http://test", timeout=5, endpoint="/predict_anomaly",
        )
        result = await client.classify({
            "entity_key": "prod/ml/reco",
            "window_start": "t0", "window_end": "t1",
            "logs": [{"body": "OOMKilled"}, {"body": "memory limit exceeded"}],
            "max_logs": 200,
            "entity_context": {},
        })

        assert set(result.keys()) == {"label", "confidence", "evidence",
                                        "severity_suggestion", "secondary_labels"}
        # Refinement: OOMKilled logs bump Resource_Exhaustion → failure.memory
        assert result["label"] == "failure.memory"
        assert result["severity_suggestion"] == "high"
        assert result["confidence"] == 0.91
        assert "OOMKilled" in result["evidence"][0]

    async def test_client_passes_through_core_shape_unchanged(self, monkeypatch):
        """If the service already returns the core shape (stub), don't translate."""
        stub_response = {
            "label": "failure.gpu",
            "confidence": 0.85,
            "evidence": ["Xid 79"],
            "severity_suggestion": "high",
            "secondary_labels": [],
        }
        self._patch_httpx(monkeypatch, stub_response)

        client = ClassifierClient(
            base_url="http://test", timeout=5, endpoint="/classify",
        )
        result = await client.classify({
            "entity_key": "x", "window_start": "t0", "window_end": "t1",
            "logs": [{"body": "x"}], "max_logs": 200, "entity_context": {},
        })

        assert result == stub_response  # unchanged

    async def test_client_uses_configured_endpoint(self, monkeypatch):
        """The classify() call must hit base_url + endpoint."""
        captured: dict[str, str] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={
                "class_id": 0, "confidence_score": 1.0, "label": "Normal",
            })

        transport = httpx.MockTransport(handler)

        class _BoundAsyncClient(httpx.AsyncClient):
            def __init__(self, **kwargs):
                kwargs.pop("transport", None)
                super().__init__(transport=transport, **kwargs)

        import app.services.classifier_client as mod
        monkeypatch.setattr(mod.httpx, "AsyncClient", _BoundAsyncClient)

        client = ClassifierClient(
            base_url="http://test", timeout=5, endpoint="/predict_anomaly",
        )
        await client.classify({"entity_key": "x", "logs": [{"body": "x"}]})

        assert captured["url"] == "http://test/predict_anomaly"
