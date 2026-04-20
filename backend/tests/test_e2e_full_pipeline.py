"""Full end-to-end integration test: Design → Runtime → Resolution.

Exercises the complete AutoMend pipeline in a single test:
  Flow A (partial): Create playbook → save version → publish
  Flow B: Logs → window worker → classifier → correlation → Temporal workflow → incident resolved

Requires Postgres on 5432 and Redis on 6380.
Uses Temporal test environment (no Docker Temporal).
"""

from __future__ import annotations

import asyncio
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
    import psycopg2
    from passlib.context import CryptContext
    from fastapi.testclient import TestClient
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    import sqlalchemy as sa

    from app.config import Settings
    from app.models.db import Base
    from app.stores import postgres_store as pg
    from app.stores import redis_store as rs
    from app.workers.window_worker import WindowWorker
    from app.workers.correlation_worker import CorrelationWorker
    from app.temporal.workflows import DynamicPlaybookExecutor
    from app.temporal.activities import (
        load_playbook_activity,
        record_step_result_activity,
        resolve_incident_activity,
        update_incident_status_activity,
    )
    from main_api import create_app

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False

REDIS_PORT = 6380
PG_URL = "postgresql+asyncpg://automend:automend@localhost:5432/automend"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _infra_available() -> bool:
    if not _HAS_DEPS:
        return False
    try:
        s = socket.create_connection(("localhost", REDIS_PORT), timeout=2)
        s.close()
    except Exception:
        return False
    try:
        conn = psycopg2.connect(
            dbname="automend", user="automend", password="automend",
            host="localhost", port=5432, connect_timeout=3,
        )
        conn.close()
    except Exception:
        return False
    return True


_infra_up = _infra_available()
pytestmark = pytest.mark.skipif(not _infra_up, reason="Postgres+Redis not available")


# ---------------------------------------------------------------------------
# Mock tool activities (no real K8s/Slack)
# ---------------------------------------------------------------------------


@activity.defn(name="fetch_pod_logs_activity")
async def mock_fetch_pod_logs(input: dict) -> dict:
    return {"logs": "CUDA error: out of memory\nGPU allocation failed", "line_count": 2}


@activity.defn(name="slack_notification_activity")
async def mock_slack_notification(input: dict) -> dict:
    return {"success": True, "ts": "e2e-ts"}


TEMPORAL_ACTIVITIES = [
    load_playbook_activity,
    update_incident_status_activity,
    resolve_incident_activity,
    record_step_result_activity,
    mock_fetch_pod_logs,
    mock_slack_notification,
]

TASK_QUEUE = "e2e-full-queue"


# ---------------------------------------------------------------------------
# Mock classifier
# ---------------------------------------------------------------------------


class MockClassifier:
    async def classify(self, input_data: dict) -> dict:
        return {
            "label": "failure.memory",
            "confidence": 0.94,
            "evidence": ["CUDA error: out of memory"],
            "severity_suggestion": "high",
            "secondary_labels": [],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_admin(email: str, password: str) -> None:
    conn = psycopg2.connect(
        dbname="automend", user="automend", password="automend",
        host="localhost", port=5432,
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (id, email, hashed_password, role, is_active, created_at) "
        "VALUES (gen_random_uuid(), %s, %s, 'admin', true, now()) ON CONFLICT (email) DO NOTHING",
        (email, pwd_context.hash(password)),
    )
    cur.close()
    conn.close()


def _settings() -> Settings:
    return Settings(
        worker_id="e2e-full-0",
        window_size_seconds=300,
        max_window_entries=500,
        window_check_interval_seconds=60,
        classifier_confidence_threshold=0.7,
        dedup_cooldown_seconds=900,
        incident_cooldown_seconds=900,
        temporal_task_queue=TASK_QUEUE,
        redis_host="localhost",
        redis_port=REDIS_PORT,
    )


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


class TestFullPipeline:
    async def test_design_to_runtime_to_resolution(self):
        """Full E2E: create playbook via API → publish → logs → classify →
        correlate → Temporal workflow → incident resolved.

        This is the combined Flow A + Flow B from §27.
        """
        settings = _settings()
        suffix = uuid4().hex[:8]
        incident_type = f"incident.gpu_memory_{suffix}"
        entity_key = f"prod-a/ml/e2e-full-{suffix}"

        # ==========================================
        # PHASE 1: Design-time — create and publish playbook via HTTP API
        # ==========================================

        admin_email = f"admin_e2e_{suffix}@test.com"
        _seed_admin(admin_email, "pw")

        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            # Login
            login_resp = client.post("/api/auth/login", json={"email": admin_email, "password": "pw"})
            assert login_resp.status_code == 200, login_resp.json()
            token = login_resp.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            # Create playbook
            pb_resp = client.post("/api/playbooks", json={
                "name": f"GPU Recovery E2E {suffix}",
                "description": "Full E2E test playbook",
            }, headers=headers)
            assert pb_resp.status_code == 201, pb_resp.json()
            playbook_id = pb_resp.json()["id"]

            # Save version with workflow spec
            workflow_spec = {
                "name": f"GPU Recovery E2E {suffix}",
                "version": "1.0.0",
                "trigger": {"incident_types": [incident_type]},
                "steps": [
                    {"id": "fetch_logs", "name": "Fetch Logs", "type": "action",
                     "tool": "fetch_pod_logs",
                     "input": {"namespace": "${incident.entity.namespace}", "pod": "trainer"}},
                    {"id": "notify", "name": "Notify Team", "type": "action",
                     "tool": "slack_notification",
                     "input": {"channel": "#ops", "message": "GPU OOM resolved"}},
                ],
                "on_complete": {"resolve_incident": True},
            }

            # Need these tools in DB for validation — seed if not present
            for tool_name in ["fetch_pod_logs", "slack_notification"]:
                conn = psycopg2.connect(
                    dbname="automend", user="automend", password="automend",
                    host="localhost", port=5432,
                )
                conn.autocommit = True
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO tools (id, name, display_name, description, category, "
                    "input_schema, output_schema, side_effect_level, required_approvals, "
                    "environments_allowed, embedding_text, is_active, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), %s, %s, %s, 'test', "
                    "'{}'::jsonb, '{}'::jsonb, 'read', 0, "
                    "'{production}', %s, true, now(), now()) ON CONFLICT (name) DO NOTHING",
                    (tool_name, tool_name, tool_name, tool_name),
                )
                cur.close()
                conn.close()

            # Validate the spec
            val_resp = client.post("/api/design/validate_workflow",
                                   json={"workflow_spec": workflow_spec}, headers=headers)
            assert val_resp.status_code == 200
            assert val_resp.json()["valid"] is True, val_resp.json()["errors"]

            # Save version
            ver_resp = client.post(f"/api/playbooks/{playbook_id}/versions", json={
                "workflow_spec": workflow_spec,
                "trigger_bindings": {"incident_types": [incident_type]},
                "change_notes": "E2E test version",
            }, headers=headers)
            assert ver_resp.status_code == 201, ver_resp.json()
            version_id = ver_resp.json()["id"]

            # Publish: draft → validated → approved → published
            for next_status in ["validated", "approved", "published"]:
                status_resp = client.patch(
                    f"/api/playbooks/{playbook_id}/versions/{version_id}/status",
                    json={"new_status": next_status},
                    headers=headers,
                )
                assert status_resp.status_code == 200, f"{next_status}: {status_resp.json()}"

        # Create trigger rule (maps incident_type to this playbook version)
        eng = create_async_engine(PG_URL, echo=False)
        factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await pg.create_trigger_rule(
                session,
                incident_type=incident_type,
                playbook_version_id=UUID(version_id),
                priority=10,
            )
            await session.commit()

        # ==========================================
        # PHASE 2: Runtime — logs → classify → correlate → workflow → resolve
        # ==========================================

        redis = Redis.from_url(f"redis://localhost:{REDIS_PORT}/6", decode_responses=True)

        try:
            # --- Window Worker: accumulate logs + classify ---
            classifier = MockClassifier()
            window_worker = WindowWorker(settings, redis, classifier)

            logs = [
                "CUDA error: out of memory",
                "Failed to allocate 4096MB on GPU 2",
                "Training step 512 aborted",
            ]
            for body in logs:
                log_entry = json.dumps({
                    "entity_key": entity_key,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "body": body,
                    "severity": "ERROR",
                    "attributes": json.dumps({
                        "cluster": "prod-a", "namespace": "ml", "service": "trainer",
                    }),
                })
                await rs.add_log_to_window(redis, entity_key, log_entry)

            classified_event = await window_worker.close_window(entity_key)
            assert classified_event is not None
            assert classified_event["classification"]["label"] == "failure.memory"

            # --- Correlation Worker + Temporal: create incident + run workflow ---
            async with await WorkflowEnvironment.start_time_skipping() as temporal_env:
                async with Worker(
                    temporal_env.client,
                    task_queue=TASK_QUEUE,
                    workflows=[DynamicPlaybookExecutor],
                    activities=TEMPORAL_ACTIVITIES,
                ):
                    correlation_worker = CorrelationWorker(
                        settings, redis, factory, temporal=temporal_env.client,
                    )

                    # Build signal from classified event
                    from app.workers.correlation_worker import _normalize_classified_event
                    # Serialize event fields as the stream would
                    stream_fields = {
                        "event_id": classified_event["event_id"],
                        "event_type": classified_event["event_type"],
                        "entity_key": classified_event["entity_key"],
                        "entity": json.dumps(classified_event["entity"]),
                        "classification": json.dumps(classified_event["classification"]),
                        "window": json.dumps(classified_event["window"]),
                        "timestamp": classified_event["timestamp"],
                    }
                    signal = _normalize_classified_event(stream_fields)
                    signal["incident_type_hint"] = incident_type  # Match our trigger rule

                    result = await correlation_worker.process_signal(signal)

                    assert result["action"] == "incident_created_workflow_started", result
                    incident_id = result["incident_id"]
                    workflow_id = result["workflow_id"]

                    # Wait for workflow to complete
                    handle = temporal_env.client.get_workflow_handle(workflow_id)
                    wf_result = await handle.result()
                    assert wf_result["status"] == "completed"
                    assert "fetch_logs" in wf_result["steps_executed"]
                    assert "notify" in wf_result["steps_executed"]

            # ==========================================
            # PHASE 3: Verify final state
            # ==========================================

            async with factory() as session:
                incident = await pg.get_incident(session, UUID(incident_id))
                assert incident is not None
                assert incident.status == "resolved"
                assert incident.resolved_at is not None
                assert incident.temporal_workflow_id == workflow_id
                assert incident.incident_type == incident_type
                assert incident.severity == "high"

                events = await pg.get_incident_events(session, UUID(incident_id))
                event_types = [e.event_type for e in events]
                assert "created" in event_types
                assert "workflow_started" in event_types
                assert "step_completed" in event_types

                # Should have step results for both steps
                step_events = [e for e in events if e.event_type == "step_completed"]
                step_ids = [e.payload.get("step_id") for e in step_events]
                assert "fetch_logs" in step_ids
                assert "notify" in step_ids

        finally:
            async for key in redis.scan_iter(match="automend:*", count=500):
                await redis.delete(key)
            await redis.aclose()
            await eng.dispose()
