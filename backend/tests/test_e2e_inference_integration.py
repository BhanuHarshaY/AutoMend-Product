"""End-to-end integration tests against the real inference services.

Each test class skips cleanly when its target service is unreachable — the tests
are meant to run in one of two modes:

* **Model-less dev machines:** everything skips. CI on laptops stays green while
  the inference services aren't running.
* **Integration environments:** bring up
  ``inference_backend/ClassifierModel/classifierModelAPI`` + the Qwen proxy
  (or ``mock_proxy.py``), optionally Postgres + Redis, and the real HTTP wire
  contracts light up.

No mocks. When a service is up, ``ClassifierClient`` and
``ArchitectClient(provider="local")`` hit it for real via ``httpx``. Assertions
are shape-only — the RoBERTa service ships with a randomly initialized
classifier head in dev mode (predictions are meaningless), and the Qwen proxy
can be backed by a mock in no-GPU environments. The test's purpose is to lock
in the wire contracts (request/response shapes, translation-layer output, the
``provider="local"`` envelope), not the model's semantic quality.

Run:
    # With both services up on localhost:8001 / :8002:
    AUTOMEND_CLASSIFIER_SERVICE_URL=http://localhost:8001 \\
    AUTOMEND_CLASSIFIER_ENDPOINT=/predict_anomaly \\
    AUTOMEND_ARCHITECT_PROVIDER=local \\
    AUTOMEND_ARCHITECT_API_BASE_URL=http://localhost:8002 \\
    pytest tests/test_e2e_inference_integration.py -v
"""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

try:
    from app.config import get_settings
    from app.services.architect_client import ArchitectClient
    from app.services.classifier_client import ClassifierClient

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False


REDIS_PORT = 6380
PG_PORT = 5432


# ---------------------------------------------------------------------------
# Reachability probes (run once at module import)
# ---------------------------------------------------------------------------


def _health_ok(url: str, timeout: float = 2.0) -> bool:
    """True iff GET {url}/health returns 200 within ``timeout`` seconds."""
    try:
        resp = httpx.get(f"{url.rstrip('/')}/health", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


def _classifier_up() -> bool:
    if not _HAS_DEPS:
        return False
    settings = get_settings()
    return _health_ok(settings.classifier_service_url)


def _architect_local_up() -> bool:
    """True iff the provider is set to 'local' AND its /health responds."""
    if not _HAS_DEPS:
        return False
    settings = get_settings()
    if settings.architect_provider.lower() != "local":
        return False
    return _health_ok(settings.architect_api_base_url)


def _tcp_up(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def _infra_up() -> bool:
    """Postgres on 5432 + Redis on 6380 — matches the existing e2e skip pattern."""
    return _tcp_up("localhost", PG_PORT) and _tcp_up("localhost", REDIS_PORT)


_classifier_reachable = _classifier_up()
_architect_reachable = _architect_local_up()
_infra_reachable = _infra_up()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _sample_log_window(bodies: list[str], entity_key: str = "prod/ml/reco") -> dict:
    """Build a classifier_input dict matching WindowWorker's output shape."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "entity_key": entity_key,
        "window_start": now,
        "window_end": now,
        "logs": [{"body": b, "attributes": {}} for b in bodies],
        "max_logs": 200,
        "entity_context": {"namespace": "prod", "workload": "reco"},
    }


# ===================================================================
# 1. Classifier — real RoBERTa service via ClassifierClient
# ===================================================================


@pytest.mark.skipif(
    not _classifier_reachable,
    reason=(
        "Classifier service not reachable at AUTOMEND_CLASSIFIER_SERVICE_URL. "
        "Start inference_backend/ClassifierModel/classifierModelAPI on port 8001 "
        "(set AUTOMEND_CLASSIFIER_ENDPOINT=/predict_anomaly for the RoBERTa service)."
    ),
)
class TestRealClassifier:
    """Drive ClassifierClient against a live classifier. Shape-only assertions.

    Works against either backend:
      * The rule-based stub (/classify) — meaningful labels, stable across runs.
      * The RoBERTa service (/predict_anomaly) — meaningless labels until the
        classifier head is trained, but the translation layer still runs.
    """

    async def test_classify_oom_window_returns_core_shape(self):
        client = ClassifierClient()
        result = await client.classify(_sample_log_window([
            "pod OOMKilled",
            "container out of memory, exit code 137",
            "cgroup memory limit exceeded",
        ]))

        # Core 14-label response envelope
        assert set(result.keys()) == {"label", "confidence", "evidence",
                                        "severity_suggestion", "secondary_labels"}
        assert isinstance(result["label"], str)
        assert 0.0 <= float(result["confidence"]) <= 1.0
        assert isinstance(result["evidence"], list)
        assert result["severity_suggestion"] in {"info", "low", "medium", "high", "critical", None}

    async def test_classify_gpu_window(self):
        client = ClassifierClient()
        result = await client.classify(_sample_log_window([
            "CUDA error: device-side assert triggered",
            "NVML: Xid (PCI:0000:03:00): 31",
        ]))
        assert isinstance(result["label"], str)

    async def test_classify_innocuous_window(self):
        client = ClassifierClient()
        result = await client.classify(_sample_log_window([
            "INFO: health check passed",
            "INFO: request processed in 87ms",
        ]))
        assert isinstance(result["label"], str)
        # Severity key present regardless
        assert "severity_suggestion" in result


# ===================================================================
# 2. Architect — Qwen vLLM proxy via ArchitectClient(provider="local")
# ===================================================================


@pytest.mark.skipif(
    not _architect_reachable,
    reason=(
        "Architect local proxy not reachable. Set AUTOMEND_ARCHITECT_PROVIDER=local "
        "and AUTOMEND_ARCHITECT_API_BASE_URL=http://localhost:8002 (or mock_proxy)."
    ),
)
class TestRealArchitect:
    """Drive ArchitectClient(provider='local') against the Qwen proxy."""

    _MINIMAL_TOOLS = [
        {
            "name": "scale_deployment",
            "description": "Scale a Kubernetes deployment to N replicas",
            "side_effect_level": "write",
            "input_schema": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "workload": {"type": "string"},
                    "replicas": {"type": "integer"},
                },
            },
            "required_approvals": 0,
        },
        {
            "name": "slack_notification",
            "description": "Send a Slack message",
            "side_effect_level": "write",
            "input_schema": {"type": "object"},
            "required_approvals": 0,
        },
    ]

    async def test_generate_scale_workflow_returns_dict(self):
        client = ArchitectClient()
        result = await client.generate_workflow(
            intent="If GPU memory is under pressure, scale up the recommendation service and notify #mlops.",
            tools=self._MINIMAL_TOOLS,
            target_incident_types=["incident.memory"],
        )

        # Shape only — the proxy's guardrails + core's _validate_spec enforce
        # semantic correctness elsewhere.
        assert isinstance(result, dict)
        assert len(result) > 0

    async def test_generate_with_policies_and_examples(self):
        client = ArchitectClient()
        result = await client.generate_workflow(
            intent="Restart a crashed pod",
            tools=self._MINIMAL_TOOLS,
            example_playbooks=None,
            policies=["Always notify the on-call channel before destructive actions."],
        )
        assert isinstance(result, dict)


# ===================================================================
# 3. Integrated pipeline — logs → classifier → incident in Postgres
# ===================================================================


@pytest.mark.skipif(
    not (_classifier_reachable and _infra_reachable),
    reason=(
        "Needs classifier service + Postgres (5432) + Redis (6380). "
        "Architect is optional for this test; it only exercises Flow B/C."
    ),
)
class TestIntegratedPipeline:
    """Drive a log window through the real classifier into a DB incident.

    This is the Flow B/C smoke: it does NOT go as far as Temporal workflow
    execution (that's already covered by test_e2e_full_pipeline.py with mocks).
    The goal here is to verify the taxonomy translation works with a live
    classifier's response shape and an incident lands in Postgres with the
    translated label.
    """

    async def test_classifier_response_flows_into_incident(self):
        # Lazy imports so non-integration runs don't need asyncpg etc.
        from uuid import UUID

        from redis.asyncio import Redis
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.config import Settings
        from app.stores import postgres_store as pg
        from app.workers.correlation_worker import CorrelationWorker

        settings = Settings()
        pg_url = "postgresql+asyncpg://automend:automend@localhost:5432/automend"
        engine = create_async_engine(pg_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        redis = Redis(host="localhost", port=REDIS_PORT, decode_responses=True)

        try:
            # Step 1: classify a realistic window against the live service.
            client = ClassifierClient()
            entity_key = f"prod/ml/reco-{uuid4().hex[:8]}"
            classifier_input = _sample_log_window(
                ["pod OOMKilled", "memory limit exceeded for reco-pod"],
                entity_key=entity_key,
            )
            result = await client.classify(classifier_input)

            # Step 2: hand the classified event to the CorrelationWorker. The
            # signal shape matches what `_normalize_classified_event` produces.
            # No Temporal client → incident is created but no workflow starts.
            worker = CorrelationWorker(settings, redis, session_factory, temporal=None)
            signal = {
                "signal_id": uuid4().hex,
                "signal_type": "classifier_output",
                "source": "log_classifier",
                "entity_key": entity_key,
                "entity": {"namespace": "prod", "workload": "reco"},
                "incident_type_hint": f"incident.{result['label']}",
                "severity": result.get("severity_suggestion") or "medium",
                "payload": {
                    "classification": result,
                    "window": {"start": classifier_input["window_start"],
                                "end": classifier_input["window_end"]},
                },
            }
            outcome = await worker.process_signal(signal)
            assert outcome is not None
            assert outcome.get("action") in {"incident_created",
                                                "incident_created_workflow_started"}
            assert "incident_id" in outcome

            # Step 3: the incident exists in Postgres with the translated label.
            async with session_factory() as session:
                incident = await pg.get_incident(session, UUID(outcome["incident_id"]))
                assert incident is not None
                assert incident.incident_type == f"incident.{result['label']}"
        finally:
            await redis.aclose()
            await engine.dispose()
