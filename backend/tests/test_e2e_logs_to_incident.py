"""End-to-end test: Logs → Window → Classifier → Correlation → Incident.

Exercises the full Flow B (§27) without Temporal:
1. Push normalized logs to Redis stream
2. WindowWorker accumulates and closes the window
3. Mock classifier returns a classification
4. CorrelationWorker creates an incident in Postgres
5. Verify the incident, events, and Redis caches

Requires Postgres on 5432 and Redis on 6380.
"""

from __future__ import annotations

import asyncio
import json
import socket
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio

try:
    import asyncpg
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    import sqlalchemy as sa

    from app.config import Settings
    from app.models.db import Base
    from app.stores import postgres_store as pg
    from app.stores import redis_store as rs
    from app.workers.window_worker import WindowWorker
    from app.workers.correlation_worker import CorrelationWorker

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False


REDIS_PORT = 6380
PG_URL = "postgresql+asyncpg://automend:automend@localhost:5432/automend"


def _infra_available() -> bool:
    if not _HAS_DEPS:
        return False
    try:
        s = socket.create_connection(("localhost", REDIS_PORT), timeout=2)
        s.close()
    except Exception:
        return False
    try:
        conn = asyncio.get_event_loop().run_until_complete(
            asyncpg.connect(user="automend", password="automend",
                            database="automend", host="localhost", port=5432, timeout=3)
        )
        asyncio.get_event_loop().run_until_complete(conn.close())
    except Exception:
        return False
    return True


_infra_up = _infra_available()
pytestmark = pytest.mark.skipif(not _infra_up, reason="Postgres+Redis not available")


# ---------------------------------------------------------------------------
# Mock classifier
# ---------------------------------------------------------------------------


class MockClassifier:
    def __init__(self, label: str, confidence: float, severity: str):
        self.label = label
        self.confidence = confidence
        self.severity = severity
        self.call_count = 0

    async def classify(self, input_data: dict) -> dict:
        self.call_count += 1
        evidence = []
        for log in input_data.get("logs", [])[:3]:
            body = log.get("body", "") if isinstance(log, dict) else str(log)
            if body:
                evidence.append(body)
        return {
            "label": self.label,
            "confidence": self.confidence,
            "evidence": evidence,
            "severity_suggestion": self.severity,
            "secondary_labels": [],
        }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _settings(**overrides) -> Settings:
    defaults = dict(
        worker_id="e2e-test-0",
        window_size_seconds=300,
        max_window_entries=500,
        window_check_interval_seconds=60,
        classifier_confidence_threshold=0.7,
        dedup_cooldown_seconds=900,
        incident_cooldown_seconds=900,
        temporal_task_queue="test-queue",
        redis_host="localhost",
        redis_port=REDIS_PORT,
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest_asyncio.fixture()
async def redis_client():
    r = Redis.from_url(f"redis://localhost:{REDIS_PORT}/4", decode_responses=True)
    yield r
    async for key in r.scan_iter(match="automend:*", count=500):
        await r.delete(key)
    await r.aclose()


@pytest_asyncio.fixture()
async def pg_factory():
    eng = create_async_engine(PG_URL, echo=False)
    async with eng.begin() as conn:
        await conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(sa.text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await eng.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_log(body: str, entity_key: str, ts: str | None = None) -> dict[str, str]:
    return {
        "entity_key": entity_key,
        "timestamp": ts or datetime.now(timezone.utc).isoformat(),
        "body": body,
        "severity": "ERROR",
        "attributes": json.dumps({"cluster": "prod-a", "namespace": "ml", "service": "trainer"}),
    }


# ===================================================================
# E2E TESTS
# ===================================================================


class TestE2ELogsToIncident:
    """Full pipeline: logs → window → classifier → correlation → incident."""

    async def test_full_flow_b(self, redis_client, pg_factory):
        """Test Flow B from §27: Logs Runtime → Incident.

        Steps:
        1. Push normalized log entries to the window (simulating OTLP ingestion)
        2. WindowWorker closes the window and calls the classifier
        3. The classified event is emitted to the classified_events stream
        4. CorrelationWorker reads the event and creates an incident
        5. Verify incident in Postgres with correct fields + events
        """
        entity_key = f"prod-a/ml/e2e-{uuid4().hex[:8]}"
        settings = _settings()

        # --- Step 1: Push logs into a window (simulating what OTLP → stream → worker does) ---
        classifier = MockClassifier(
            label="failure.memory", confidence=0.94, severity="high",
        )
        window_worker = WindowWorker(settings, redis_client, classifier)

        logs = [
            "CUDA error: out of memory",
            "Failed to allocate 4096MB on GPU 2",
            "Training step 1024 failed",
            "GPU memory allocation failed for batch size 64",
            "RuntimeError: CUDA out of memory",
        ]
        for body in logs:
            log_entry = _make_log(body, entity_key)
            await rs.add_log_to_window(redis_client, entity_key, json.dumps(log_entry))

        # Verify window exists
        meta = await rs.get_window_meta(redis_client, entity_key)
        assert int(meta["count"]) == 5

        # --- Step 2: Close the window (triggers classification) ---
        classified_event = await window_worker.close_window(entity_key)

        # Verify classification happened
        assert classifier.call_count == 1
        assert classified_event is not None
        assert classified_event["classification"]["label"] == "failure.memory"
        assert classified_event["classification"]["confidence"] == 0.94
        assert classified_event["entity_key"] == entity_key
        assert classified_event["window"]["log_count"] == 5

        # Verify classified event is in the stream
        stream_len = await rs.stream_len(redis_client, rs.STREAM_CLASSIFIED_EVENTS)
        assert stream_len >= 1

        # Verify window is cleaned up
        assert await rs.get_window_entries(redis_client, entity_key) == []

        # Verify classifier dedup was set
        assert await rs.has_classifier_dedup(redis_client, entity_key, "failure.memory")

        # --- Step 3: CorrelationWorker processes the classified event ---
        correlation_worker = CorrelationWorker(
            settings, redis_client, pg_factory, temporal=None,
        )

        # Read the classified event from the stream and process it
        await rs.ensure_consumer_group(
            redis_client, rs.STREAM_CLASSIFIED_EVENTS, rs.GROUP_CORRELATION_WORKERS,
        )
        entries = await rs.stream_read_group(
            redis_client, rs.STREAM_CLASSIFIED_EVENTS,
            rs.GROUP_CORRELATION_WORKERS, "e2e-consumer",
            count=10, block=500,
        )
        assert len(entries) >= 1

        # Normalize and process the first classified event
        from app.workers.correlation_worker import _normalize_classified_event
        entry_id, fields = entries[0]
        signal = _normalize_classified_event(fields)
        result = await correlation_worker.process_signal(signal)

        # --- Step 4: Verify incident was created ---
        assert result["action"] == "incident_created"
        incident_id = result["incident_id"]
        incident_key = result["incident_key"]

        # Verify in Postgres
        async with pg_factory() as session:
            from uuid import UUID
            incident = await pg.get_incident(session, UUID(incident_id))
            assert incident is not None
            assert incident.incident_key == incident_key
            assert incident.incident_type == "incident.failure.memory"
            assert incident.status == "open"
            assert incident.severity == "high"
            assert incident.entity["cluster"] == "prod-a"
            assert "log_classifier" in incident.sources

            # Verify events
            events = await pg.get_incident_events(session, UUID(incident_id))
            event_types = [e.event_type for e in events]
            assert "created" in event_types
            # No playbook matched (we didn't seed any trigger rules)
            assert "no_playbook_matched" in event_types

        # Verify Redis active incident cache
        active = await rs.get_active_incident(redis_client, incident_key)
        assert active is not None
        assert active["incident_id"] == incident_id
        assert active["status"] == "open"

        # Verify incident dedup is set
        assert await rs.has_incident_dedup(redis_client, incident_key)

    async def test_flow_c_alertmanager_to_incident(self, redis_client, pg_factory):
        """Test Flow C from §27: Alertmanager webhook → Incident.

        Simulates an Alertmanager signal arriving via the correlation input stream.
        """
        entity_key = f"prod-a/ml/e2e-alert-{uuid4().hex[:8]}"
        incident_type = "incident.gpu_memory_failure"
        settings = _settings()

        # Push an alertmanager-style signal to the correlation input stream
        from app.api.routes_webhooks import transform_alertmanager_alert
        alert = {
            "status": "firing",
            "labels": {
                "alertname": "GPUHighMemoryPressure",
                "severity": "high",
                "incident_type": incident_type,
                "namespace": "ml",
                "service": entity_key.split("/")[-1],
            },
            "annotations": {
                "summary": "GPU memory above 95%",
                "entity_cluster": "prod-a",
            },
            "startsAt": datetime.now(timezone.utc).isoformat(),
        }
        signal_data = transform_alertmanager_alert(alert)
        await rs.stream_add(redis_client, rs.STREAM_CORRELATION_INPUT, signal_data, maxlen=10000)

        # Read and process
        correlation_worker = CorrelationWorker(
            settings, redis_client, pg_factory, temporal=None,
        )
        await rs.ensure_consumer_group(
            redis_client, rs.STREAM_CORRELATION_INPUT, rs.GROUP_CORRELATION_WORKERS,
        )
        entries = await rs.stream_read_group(
            redis_client, rs.STREAM_CORRELATION_INPUT,
            rs.GROUP_CORRELATION_WORKERS, "e2e-alert-consumer",
            count=10, block=500,
        )
        assert len(entries) >= 1

        from app.workers.correlation_worker import _normalize_correlation_input
        _, fields = entries[0]
        signal = _normalize_correlation_input(fields)
        result = await correlation_worker.process_signal(signal)

        assert result["action"] == "incident_created"

        # Verify incident in DB
        async with pg_factory() as session:
            from uuid import UUID
            inc = await pg.get_incident(session, UUID(result["incident_id"]))
            assert inc is not None
            assert inc.incident_type == incident_type
            assert inc.severity == "high"
            assert "alertmanager" in inc.sources

    async def test_flow_d_combined_signals_correlate(self, redis_client, pg_factory):
        """Test Flow D from §27: Two signals correlate into one incident.

        First signal (classifier) creates the incident.
        Second signal (alertmanager) adds to the existing incident.
        """
        entity_key = f"prod-a/ml/e2e-combined-{uuid4().hex[:8]}"
        incident_type = "incident.failure.memory"
        settings = _settings()

        correlation_worker = CorrelationWorker(
            settings, redis_client, pg_factory, temporal=None,
        )

        # First signal: classifier output
        signal_1 = {
            "signal_id": str(uuid4()),
            "signal_type": "classifier_output",
            "source": "log_classifier",
            "entity_key": entity_key,
            "entity": {"cluster": "prod-a", "namespace": "ml", "service": "trainer"},
            "incident_type_hint": incident_type,
            "severity": "high",
            "payload": {"classification": {"label": "failure.memory", "confidence": 0.94}},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        r1 = await correlation_worker.process_signal(signal_1)
        assert r1["action"] == "incident_created"

        # Second signal: alertmanager (same entity_key + incident_type → same incident_key)
        signal_2 = {
            "signal_id": str(uuid4()),
            "signal_type": "prometheus_alert",
            "source": "alertmanager",
            "entity_key": entity_key,
            "entity": {"cluster": "prod-a", "namespace": "ml", "service": "trainer"},
            "incident_type_hint": incident_type,
            "severity": "high",
            "payload": {"alert_name": "GPUHighMemoryPressure"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        r2 = await correlation_worker.process_signal(signal_2)
        assert r2["action"] == "signal_added"
        assert r2["incident_id"] == r1["incident_id"]  # Same incident!

        # Verify events show both signals
        async with pg_factory() as session:
            from uuid import UUID
            events = await pg.get_incident_events(session, UUID(r1["incident_id"]))
            event_types = [e.event_type for e in events]
            assert "created" in event_types
            assert "signal_added" in event_types

    async def test_dedup_prevents_second_incident(self, redis_client, pg_factory):
        """After creating an incident, the dedup key prevents a new one
        even after the active incident cache is removed."""
        entity_key = f"prod-a/ml/e2e-dedup-{uuid4().hex[:8]}"
        settings = _settings()

        worker = CorrelationWorker(settings, redis_client, pg_factory, temporal=None)

        signal = {
            "signal_id": str(uuid4()),
            "signal_type": "classifier_output",
            "source": "log_classifier",
            "entity_key": entity_key,
            "entity": {"cluster": "prod-a"},
            "incident_type_hint": "incident.test",
            "severity": "medium",
            "payload": {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        r1 = await worker.process_signal(signal)
        assert r1["action"] == "incident_created"

        # Remove active incident cache (simulating resolution cleanup)
        ik = r1["incident_key"]
        await rs.delete_active_incident(redis_client, ik)

        # Try again — dedup should prevent a new incident
        signal["signal_id"] = str(uuid4())
        r2 = await worker.process_signal(signal)
        assert r2["action"] == "suppressed_dedup"
