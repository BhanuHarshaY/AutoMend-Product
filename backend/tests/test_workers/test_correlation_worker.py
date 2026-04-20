"""Tests for CorrelationWorker (§11).

Requires Postgres on 5432 and Redis on 6380. Skips if either is unavailable.
Uses mock Temporal client.
"""

from __future__ import annotations

import asyncio
import json
import socket
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
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
    from app.workers.correlation_worker import CorrelationWorker

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False

REDIS_PORT = 6380
PG_URL = "postgresql+asyncpg://automend:automend@localhost:5432/automend"


def _infra_available() -> bool:
    if not _HAS_DEPS:
        return False
    # Redis
    try:
        s = socket.create_connection(("localhost", REDIS_PORT), timeout=2)
        s.close()
    except Exception:
        return False
    # Postgres
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
# Fixtures
# ---------------------------------------------------------------------------


def _test_settings(**overrides) -> Settings:
    defaults = dict(
        worker_id="corr-test-0",
        temporal_task_queue="test-queue",
        incident_cooldown_seconds=900,
        dedup_cooldown_seconds=900,
        redis_host="localhost",
        redis_port=REDIS_PORT,
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest_asyncio.fixture()
async def redis_client():
    r = Redis.from_url(f"redis://localhost:{REDIS_PORT}/3", decode_responses=True)
    yield r
    async for key in r.scan_iter(match="automend:*", count=500):
        await r.delete(key)
    await r.aclose()


@pytest_asyncio.fixture()
async def pg_session_factory():
    eng = create_async_engine(PG_URL, echo=False)
    async with eng.begin() as conn:
        await conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(sa.text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await eng.dispose()


def _make_signal(
    entity_key: str = "prod/ml/trainer",
    incident_type: str = "incident.failure.memory",
    severity: str = "high",
    source: str = "log_classifier",
) -> dict:
    return {
        "signal_id": str(uuid4()),
        "signal_type": "classifier_output",
        "source": source,
        "entity_key": entity_key,
        "entity": {"cluster": "prod", "namespace": "ml", "service": "trainer"},
        "incident_type_hint": incident_type,
        "severity": severity,
        "payload": {"classification": {"label": "failure.memory", "confidence": 0.94}},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _mock_temporal():
    mock = MagicMock()
    handle = MagicMock()
    handle.run_id = "run-123"
    handle.signal = AsyncMock()
    mock.start_workflow = AsyncMock(return_value=handle)
    mock.get_workflow_handle = MagicMock(return_value=handle)
    return mock


# ===================================================================
# NEW INCIDENT CREATION
# ===================================================================


class TestNewIncident:
    async def test_creates_incident(self, redis_client, pg_session_factory):
        settings = _test_settings()
        worker = CorrelationWorker(settings, redis_client, pg_session_factory)

        signal = _make_signal(entity_key=f"test/{uuid4().hex[:8]}")
        result = await worker.process_signal(signal)

        assert result["action"] == "incident_created"
        assert result["incident_id"] is not None
        assert result["playbook_matched"] is False

        # Verify incident in DB
        async with pg_session_factory() as session:
            from uuid import UUID
            inc = await pg.get_incident(session, UUID(result["incident_id"]))
            assert inc is not None
            assert inc.status == "open"
            assert inc.severity == "high"

    async def test_creates_event(self, redis_client, pg_session_factory):
        settings = _test_settings()
        worker = CorrelationWorker(settings, redis_client, pg_session_factory)

        signal = _make_signal(entity_key=f"test/{uuid4().hex[:8]}")
        result = await worker.process_signal(signal)

        async with pg_session_factory() as session:
            from uuid import UUID
            events = await pg.get_incident_events(session, UUID(result["incident_id"]))
            types = [e.event_type for e in events]
            assert "created" in types

    async def test_sets_active_incident_cache(self, redis_client, pg_session_factory):
        settings = _test_settings()
        worker = CorrelationWorker(settings, redis_client, pg_session_factory)

        ek = f"test/{uuid4().hex[:8]}"
        signal = _make_signal(entity_key=ek)
        result = await worker.process_signal(signal)

        ik = f"{ek}/{signal['incident_type_hint']}"
        active = await rs.get_active_incident(redis_client, ik)
        assert active is not None
        assert active["incident_id"] == result["incident_id"]

    async def test_sets_dedup(self, redis_client, pg_session_factory):
        settings = _test_settings()
        worker = CorrelationWorker(settings, redis_client, pg_session_factory)

        ek = f"test/{uuid4().hex[:8]}"
        signal = _make_signal(entity_key=ek)
        await worker.process_signal(signal)

        ik = f"{ek}/{signal['incident_type_hint']}"
        assert await rs.has_incident_dedup(redis_client, ik) is True


# ===================================================================
# DEDUP AND COOLDOWN
# ===================================================================


class TestSuppression:
    async def test_cooldown_suppresses(self, redis_client, pg_session_factory):
        settings = _test_settings()
        worker = CorrelationWorker(settings, redis_client, pg_session_factory)

        ek = f"test/{uuid4().hex[:8]}"
        signal = _make_signal(entity_key=ek)
        ik = f"{ek}/{signal['incident_type_hint']}"
        await rs.set_cooldown(redis_client, ik, ttl=60)

        result = await worker.process_signal(signal)
        assert result["action"] == "suppressed_cooldown"

    async def test_dedup_suppresses(self, redis_client, pg_session_factory):
        settings = _test_settings()
        worker = CorrelationWorker(settings, redis_client, pg_session_factory)

        ek = f"test/{uuid4().hex[:8]}"
        signal = _make_signal(entity_key=ek)
        ik = f"{ek}/{signal['incident_type_hint']}"
        await rs.set_incident_dedup(redis_client, ik, "inc-old", ttl=60)

        result = await worker.process_signal(signal)
        assert result["action"] == "suppressed_dedup"


# ===================================================================
# EXISTING INCIDENT — SIGNAL ADDED
# ===================================================================


class TestExistingIncident:
    async def test_adds_signal_to_existing(self, redis_client, pg_session_factory):
        settings = _test_settings()
        worker = CorrelationWorker(settings, redis_client, pg_session_factory)

        ek = f"test/{uuid4().hex[:8]}"
        signal1 = _make_signal(entity_key=ek)

        # First signal creates incident
        r1 = await worker.process_signal(signal1)
        assert r1["action"] == "incident_created"

        # Second signal should add to existing
        signal2 = _make_signal(entity_key=ek, source="alertmanager")
        r2 = await worker.process_signal(signal2)
        assert r2["action"] == "signal_added"
        assert r2["incident_id"] == r1["incident_id"]

    async def test_severity_escalation(self, redis_client, pg_session_factory):
        settings = _test_settings()
        worker = CorrelationWorker(settings, redis_client, pg_session_factory)

        ek = f"test/{uuid4().hex[:8]}"
        signal1 = _make_signal(entity_key=ek, severity="medium")
        r1 = await worker.process_signal(signal1)

        # Higher severity signal arrives
        signal2 = _make_signal(entity_key=ek, severity="critical")
        await worker.process_signal(signal2)

        # Verify escalation
        async with pg_session_factory() as session:
            from uuid import UUID
            inc = await pg.get_incident(session, UUID(r1["incident_id"]))
            assert inc.severity == "critical"

    async def test_signals_temporal_workflow(self, redis_client, pg_session_factory):
        settings = _test_settings()
        temporal = _mock_temporal()
        worker = CorrelationWorker(settings, redis_client, pg_session_factory, temporal)

        ek = f"test/{uuid4().hex[:8]}"
        itype = f"incident.test_{uuid4().hex[:8]}"
        ik = f"{ek}/{itype}"

        # Create a real incident in the DB so FK constraints are satisfied
        async with pg_session_factory() as session:
            inc = await pg.create_incident(
                session, incident_key=ik, incident_type=itype,
                severity="medium", entity={"cluster": "test"},
                sources=["test"], evidence={},
            )
            await session.commit()
            inc_id = str(inc.id)

        # Pre-set active incident in Redis with workflow_id
        await rs.set_active_incident(
            redis_client, ik, inc_id, status="open", workflow_id="wf-running",
        )

        signal = _make_signal(entity_key=ek, incident_type=itype)
        result = await worker.process_signal(signal)

        assert result["action"] == "signal_added"
        assert result["incident_id"] == inc_id
        temporal.get_workflow_handle.assert_called_once_with("wf-running")


# ===================================================================
# WORKFLOW START
# ===================================================================


class TestWorkflowStart:
    async def test_starts_workflow_when_playbook_matched(self, redis_client, pg_session_factory):
        settings = _test_settings()
        temporal = _mock_temporal()
        worker = CorrelationWorker(settings, redis_client, pg_session_factory, temporal)

        # Create a playbook + version + trigger rule
        async with pg_session_factory() as session:
            pb = await pg.create_playbook(session, name=f"pb_{uuid4().hex[:8]}")
            v = await pg.save_version(session, pb.id, workflow_spec={"v": 1})
            await pg.transition_version_status(session, v.id, "published")
            itype = f"incident.test_{uuid4().hex[:8]}"
            await pg.create_trigger_rule(
                session, incident_type=itype,
                playbook_version_id=v.id, priority=10,
            )
            await session.commit()

        ek = f"test/{uuid4().hex[:8]}"
        signal = _make_signal(entity_key=ek, incident_type=itype)
        result = await worker.process_signal(signal)

        assert result["action"] == "incident_created_workflow_started"
        assert result["workflow_id"] is not None
        temporal.start_workflow.assert_called_once()

    async def test_no_workflow_without_temporal(self, redis_client, pg_session_factory):
        settings = _test_settings()
        worker = CorrelationWorker(settings, redis_client, pg_session_factory, temporal=None)

        # Even with a matching rule, no workflow starts without Temporal
        async with pg_session_factory() as session:
            pb = await pg.create_playbook(session, name=f"pb_{uuid4().hex[:8]}")
            v = await pg.save_version(session, pb.id, workflow_spec={"v": 1})
            await pg.transition_version_status(session, v.id, "published")
            itype = f"incident.test_{uuid4().hex[:8]}"
            await pg.create_trigger_rule(
                session, incident_type=itype,
                playbook_version_id=v.id, priority=10,
            )
            await session.commit()

        signal = _make_signal(entity_key=f"test/{uuid4().hex[:8]}", incident_type=itype)
        result = await worker.process_signal(signal)

        assert result["action"] == "incident_created"
        assert result["playbook_matched"] is True


# ===================================================================
# KILL SWITCH — Task 11.8c: playbooks_enabled gate on the project
# ===================================================================


class TestKillSwitch:
    async def _setup_published_rule(self, session_factory):
        """Helper: create a published playbook version + a trigger rule that
        matches `incident_type`. Returns (incident_type, version_id)."""
        async with session_factory() as session:
            pb = await pg.create_playbook(session, name=f"pb_{uuid4().hex[:8]}")
            v = await pg.save_version(session, pb.id, workflow_spec={"v": 1})
            await pg.transition_version_status(session, v.id, "published")
            itype = f"incident.test_{uuid4().hex[:8]}"
            await pg.create_trigger_rule(
                session, incident_type=itype,
                playbook_version_id=v.id, priority=10,
            )
            await session.commit()
            return itype

    async def _make_project(self, session_factory, namespace: str, enabled: bool):
        async with session_factory() as session:
            p = await pg.create_project(
                session, name=f"proj_{uuid4().hex[:8]}", namespace=namespace,
                playbooks_enabled=enabled,
            )
            await session.commit()
            return p

    async def test_disabled_project_suppresses_workflow_start(
        self, redis_client, pg_session_factory,
    ):
        """Project with playbooks_enabled=false → incident still created but
        no Temporal workflow start call."""
        settings = _test_settings()
        temporal = _mock_temporal()
        worker = CorrelationWorker(settings, redis_client, pg_session_factory, temporal)

        itype = await self._setup_published_rule(pg_session_factory)
        # Unique namespace so test runs don't collide across cases.
        ns = f"kill-{uuid4().hex[:6]}"
        await self._make_project(pg_session_factory, namespace=ns, enabled=False)

        signal = _make_signal(
            entity_key=f"prod/{ns}/svc-{uuid4().hex[:4]}",
            incident_type=itype,
        )
        signal["entity"] = {"cluster": "prod", "namespace": ns, "service": "trainer"}

        result = await worker.process_signal(signal)

        assert result["action"] == "incident_created_playbooks_disabled"
        assert result["namespace"] == ns
        # Workflow start must NOT have been called.
        temporal.start_workflow.assert_not_called()

        # Incident exists in DB with the right incident_type + a recorded
        # "playbooks_disabled_for_namespace" event.
        async with pg_session_factory() as session:
            from uuid import UUID
            inc = await pg.get_incident(session, UUID(result["incident_id"]))
            assert inc is not None
            events = await pg.get_incident_events(session, UUID(result["incident_id"]))
            assert any(e.event_type == "playbooks_disabled_for_namespace" for e in events)

    async def test_enabled_project_starts_workflow(
        self, redis_client, pg_session_factory,
    ):
        """Project with playbooks_enabled=true (default) → workflow starts
        normally. Confirms the kill-switch path doesn't accidentally block
        the happy case."""
        settings = _test_settings()
        temporal = _mock_temporal()
        worker = CorrelationWorker(settings, redis_client, pg_session_factory, temporal)

        itype = await self._setup_published_rule(pg_session_factory)
        ns = f"live-{uuid4().hex[:6]}"
        await self._make_project(pg_session_factory, namespace=ns, enabled=True)

        signal = _make_signal(
            entity_key=f"prod/{ns}/svc-{uuid4().hex[:4]}",
            incident_type=itype,
        )
        signal["entity"] = {"cluster": "prod", "namespace": ns, "service": "trainer"}

        result = await worker.process_signal(signal)

        assert result["action"] == "incident_created_workflow_started"
        temporal.start_workflow.assert_called_once()

    async def test_no_project_for_namespace_still_starts_workflow(
        self, redis_client, pg_session_factory,
    ):
        """If the incident's namespace isn't owned by any AutoMend project,
        don't gate — remediation proceeds. We only block when there's an
        explicit project saying 'off'."""
        settings = _test_settings()
        temporal = _mock_temporal()
        worker = CorrelationWorker(settings, redis_client, pg_session_factory, temporal)

        itype = await self._setup_published_rule(pg_session_factory)
        # No project created for this namespace.
        signal = _make_signal(
            entity_key=f"test/{uuid4().hex[:8]}",
            incident_type=itype,
        )
        signal["entity"] = {
            "cluster": "prod",
            "namespace": f"orphan-{uuid4().hex[:6]}",
            "service": "x",
        }

        result = await worker.process_signal(signal)
        assert result["action"] == "incident_created_workflow_started"
        temporal.start_workflow.assert_called_once()


# ===================================================================
# NORMALIZATION
# ===================================================================


class TestNormalization:
    def test_normalize_classified_event(self):
        from app.workers.correlation_worker import _normalize_classified_event
        fields = {
            "event_id": "ev-1",
            "entity_key": "prod/ml/trainer",
            "entity": json.dumps({"cluster": "prod"}),
            "classification": json.dumps({"label": "failure.memory", "severity_suggestion": "high"}),
            "window": json.dumps({"start": "t0", "end": "t1"}),
            "timestamp": "2025-01-15T10:30:00Z",
        }
        sig = _normalize_classified_event(fields)
        assert sig["signal_type"] == "classifier_output"
        assert sig["incident_type_hint"] == "incident.failure.memory"
        assert sig["severity"] == "high"

    def test_normalize_correlation_input(self):
        from app.workers.correlation_worker import _normalize_correlation_input
        fields = {
            "signal_id": "sig-1",
            "signal_type": "prometheus_alert",
            "source": "alertmanager",
            "entity_key": "prod/ml/trainer",
            "entity": json.dumps({"cluster": "prod"}),
            "incident_type_hint": "incident.gpu_memory_failure",
            "severity": "high",
            "payload": json.dumps({"alert_name": "GPUHighMem"}),
            "timestamp": "2025-01-15T10:30:00Z",
        }
        sig = _normalize_correlation_input(fields)
        assert sig["signal_type"] == "prometheus_alert"
        assert sig["entity"]["cluster"] == "prod"
        assert sig["payload"]["alert_name"] == "GPUHighMem"
