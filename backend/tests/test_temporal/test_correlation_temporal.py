"""Integration test: Correlation Worker → Temporal DynamicPlaybookExecutor.

Verifies the full path: signal arrives → correlation worker creates incident →
starts Temporal workflow → workflow loads playbook → executes steps → resolves incident.

Requires Postgres on 5432 and Redis on 6380.
Uses Temporal test environment (no Docker Temporal).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

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
    from app.temporal.workflows import DynamicPlaybookExecutor

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
# Real infrastructure activities (hit Postgres)
# ---------------------------------------------------------------------------

# Import the real ones — they create their own engine internally
from app.temporal.activities import (
    load_playbook_activity,
    record_step_result_activity,
    resolve_incident_activity,
    update_incident_status_activity,
)

# Mock tool activities (no K8s/Slack needed)

@activity.defn(name="fetch_pod_logs_activity")
async def mock_fetch_pod_logs(input: dict) -> dict:
    return {"logs": "integration test logs", "line_count": 10}

@activity.defn(name="slack_notification_activity")
async def mock_slack_notification(input: dict) -> dict:
    return {"success": True, "ts": "mock-ts"}

ALL_ACTIVITIES = [
    load_playbook_activity,
    update_incident_status_activity,
    resolve_incident_activity,
    record_step_result_activity,
    mock_fetch_pod_logs,
    mock_slack_notification,
]

TASK_QUEUE = "integration-test-queue"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _settings(**overrides) -> Settings:
    defaults = dict(
        worker_id="integ-test-0",
        temporal_task_queue=TASK_QUEUE,
        incident_cooldown_seconds=900,
        dedup_cooldown_seconds=900,
        redis_host="localhost",
        redis_port=REDIS_PORT,
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest_asyncio.fixture()
async def redis_client():
    r = Redis.from_url(f"redis://localhost:{REDIS_PORT}/5", decode_responses=True)
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


def _spec_checksum(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCorrelationToTemporal:
    async def test_signal_triggers_workflow_that_resolves_incident(
        self, redis_client, pg_factory,
    ):
        """Full integration: signal → correlation → Temporal workflow → incident resolved.

        Steps:
        1. Seed a playbook + version + trigger rule in Postgres
        2. Start a Temporal worker with real infrastructure activities
        3. Send a signal through the correlation worker
        4. Correlation worker creates incident + starts workflow
        5. Workflow loads playbook, executes steps, resolves incident
        6. Verify incident is resolved in Postgres
        """
        settings = _settings()

        # --- Step 1: Seed playbook + trigger rule ---
        playbook_spec = {
            "name": "Test Recovery",
            "version": "1.0.0",
            "trigger": {"incident_types": ["incident.test_integration"]},
            "steps": [
                {"id": "fetch", "name": "Fetch Logs", "type": "action", "tool": "fetch_pod_logs",
                 "input": {"namespace": "ml", "pod": "trainer"}},
                {"id": "notify", "name": "Notify", "type": "action", "tool": "slack_notification",
                 "input": {"channel": "#ops", "message": "Done"}},
            ],
            "on_complete": {"resolve_incident": True},
        }

        incident_type = f"incident.test_integ_{uuid4().hex[:8]}"

        async with pg_factory() as session:
            pb = await pg.create_playbook(session, name=f"integ_pb_{uuid4().hex[:8]}")
            v = await pg.save_version(session, pb.id, workflow_spec=playbook_spec)
            await pg.transition_version_status(session, v.id, "published")
            await pg.create_trigger_rule(
                session, incident_type=incident_type,
                playbook_version_id=v.id, priority=10,
            )
            await session.commit()

        # --- Step 2: Start Temporal test environment + worker ---
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async with Worker(
                env.client,
                task_queue=TASK_QUEUE,
                workflows=[DynamicPlaybookExecutor],
                activities=ALL_ACTIVITIES,
            ):
                # --- Step 3: Run the correlation worker on a signal ---
                correlation_worker = CorrelationWorker(
                    settings, redis_client, pg_factory, temporal=env.client,
                )

                entity_key = f"prod/ml/integ-{uuid4().hex[:8]}"
                signal = {
                    "signal_id": str(uuid4()),
                    "signal_type": "classifier_output",
                    "source": "log_classifier",
                    "entity_key": entity_key,
                    "entity": {"cluster": "prod", "namespace": "ml", "service": "trainer"},
                    "incident_type_hint": incident_type,
                    "severity": "high",
                    "payload": {"classification": {"label": "test", "confidence": 0.9}},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

                result = await correlation_worker.process_signal(signal)

                # --- Step 4: Verify correlation created incident + started workflow ---
                assert result["action"] == "incident_created_workflow_started"
                assert result["workflow_id"] is not None
                incident_id = result["incident_id"]

                # --- Step 5: Wait for workflow to complete ---
                handle = env.client.get_workflow_handle(result["workflow_id"])
                wf_result = await handle.result()

                assert wf_result["status"] == "completed"
                assert "fetch" in wf_result["steps_executed"]
                assert "notify" in wf_result["steps_executed"]

                # --- Step 6: Verify incident is resolved in Postgres ---
                async with pg_factory() as session:
                    inc = await pg.get_incident(session, UUID(incident_id))
                    assert inc is not None
                    assert inc.status == "resolved"
                    assert inc.resolved_at is not None
                    assert inc.temporal_workflow_id == result["workflow_id"]

                    # Verify events trail
                    events = await pg.get_incident_events(session, UUID(incident_id))
                    event_types = [e.event_type for e in events]
                    assert "created" in event_types
                    assert "workflow_started" in event_types
                    assert "step_completed" in event_types
