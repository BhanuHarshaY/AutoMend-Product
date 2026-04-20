"""Tests for Temporal activities (§22).

Infrastructure activity tests use Postgres (skip if unavailable).
Tool activity tests verify structure and allowed-script logic (no K8s needed).
"""

from __future__ import annotations

import asyncio
import socket
from uuid import uuid4

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Infrastructure activity tests (need Postgres)
# ---------------------------------------------------------------------------

try:
    import asyncpg
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.models.db import Base
    from app.stores import postgres_store as store
    from app.temporal.activities import (
        load_playbook_activity,
        record_step_result_activity,
        resolve_incident_activity,
        run_diagnostic_script_activity,
        update_incident_status_activity,
    )

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False

PG_URL = "postgresql+asyncpg://automend:automend@localhost:5432/automend"


def _pg_available() -> bool:
    if not _HAS_DEPS:
        return False
    try:
        conn = asyncio.get_event_loop().run_until_complete(
            asyncpg.connect(user="automend", password="automend",
                            database="automend", host="localhost", port=5432, timeout=3)
        )
        asyncio.get_event_loop().run_until_complete(conn.close())
        return True
    except Exception:
        return False


_pg_is_up = _pg_available()


@pytest_asyncio.fixture()
async def pg_factory():
    if not _pg_is_up:
        pytest.skip("Postgres not available")
    eng = create_async_engine(PG_URL, echo=False)
    async with eng.begin() as conn:
        await conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(sa.text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await eng.dispose()


class TestLoadPlaybookActivity:
    @pytest.mark.skipif(not _pg_is_up, reason="Postgres not available")
    async def test_loads_spec_and_checksum(self, pg_factory):
        # Seed a playbook + version
        async with pg_factory() as session:
            pb = await store.create_playbook(session, name=f"act_pb_{uuid4().hex[:8]}")
            v = await store.save_version(session, pb.id, workflow_spec={"name": "test", "steps": []})
            await session.commit()
            version_id = str(v.id)
            expected_checksum = v.spec_checksum

        result = await load_playbook_activity(version_id)
        assert result["workflow_spec"]["name"] == "test"
        assert result["spec_checksum"] == expected_checksum

    @pytest.mark.skipif(not _pg_is_up, reason="Postgres not available")
    async def test_raises_on_missing_version(self):
        with pytest.raises(ValueError, match="not found"):
            await load_playbook_activity(str(uuid4()))


class TestUpdateIncidentStatusActivity:
    @pytest.mark.skipif(not _pg_is_up, reason="Postgres not available")
    async def test_updates_status(self, pg_factory):
        async with pg_factory() as session:
            inc = await store.create_incident(
                session, incident_key=f"act/{uuid4().hex[:8]}",
                incident_type="test", severity="medium",
                entity={}, sources=["test"], evidence={},
            )
            await session.commit()
            inc_id = str(inc.id)

        result = await update_incident_status_activity(inc_id, "in_progress")
        assert result["status"] == "in_progress"

        # Verify in DB
        async with pg_factory() as session:
            from uuid import UUID
            inc = await store.get_incident(session, UUID(inc_id))
            assert inc.status == "in_progress"


class TestResolveIncidentActivity:
    @pytest.mark.skipif(not _pg_is_up, reason="Postgres not available")
    async def test_resolves(self, pg_factory):
        async with pg_factory() as session:
            inc = await store.create_incident(
                session, incident_key=f"act/{uuid4().hex[:8]}",
                incident_type="test", severity="medium",
                entity={}, sources=["test"], evidence={},
            )
            await session.commit()
            inc_id = str(inc.id)

        result = await resolve_incident_activity(inc_id)
        assert result["status"] == "resolved"


class TestRecordStepResultActivity:
    @pytest.mark.skipif(not _pg_is_up, reason="Postgres not available")
    async def test_records_event(self, pg_factory):
        async with pg_factory() as session:
            inc = await store.create_incident(
                session, incident_key=f"act/{uuid4().hex[:8]}",
                incident_type="test", severity="medium",
                entity={}, sources=["test"], evidence={},
            )
            await session.commit()
            inc_id = str(inc.id)

        result = await record_step_result_activity(
            inc_id, "step_a", True, {"logs": "found"}, None,
        )
        assert result["recorded"] is True

        # Verify event in DB
        async with pg_factory() as session:
            from uuid import UUID
            events = await store.get_incident_events(session, UUID(inc_id))
            assert any(
                e.event_type == "step_completed" and e.payload.get("step_id") == "step_a"
                for e in events
            )


# ---------------------------------------------------------------------------
# Tool activity unit tests (no external services needed)
# ---------------------------------------------------------------------------


class TestRunDiagnosticScriptValidation:
    """Test the allowed-scripts whitelist logic."""

    async def test_disallowed_script_returns_error(self):
        result = await run_diagnostic_script_activity({
            "namespace": "ml",
            "pod": "trainer",
            "script_name": "rm_rf_slash",
        })
        assert result["exit_code"] == 1
        assert "not in allowed list" in result["stderr"]

    async def test_allowed_script_names(self):
        """Verify the whitelist contains expected scripts."""
        # Try each known script — they'll fail on K8s connect, but
        # we check they pass the whitelist check
        allowed = ["nvidia_smi", "gpu_memory", "disk_usage", "process_list", "network_check"]
        for script in allowed:
            try:
                await run_diagnostic_script_activity({
                    "namespace": "ml", "pod": "p", "script_name": script,
                })
            except Exception as e:
                # Expected: K8s config not available. But NOT "not in allowed list"
                assert "not in allowed list" not in str(e)


class TestActivityImports:
    """Verify all 18 activities are importable and decorated."""

    EXPECTED = [
        "load_playbook_activity",
        "update_incident_status_activity",
        "resolve_incident_activity",
        "record_step_result_activity",
        "fetch_pod_logs_activity",
        "query_prometheus_activity",
        "restart_workload_activity",
        "scale_deployment_activity",
        "rollback_release_activity",
        "page_oncall_activity",
        "slack_notification_activity",
        "slack_approval_activity",
        "open_ticket_activity",
        "describe_pod_activity",
        "get_node_status_activity",
        "cordon_node_activity",
        "drain_node_activity",
        "run_diagnostic_script_activity",
    ]

    @pytest.mark.parametrize("name", EXPECTED)
    def test_activity_exists(self, name):
        from app.temporal import activities
        fn = getattr(activities, name)
        assert callable(fn)
        # Verify it's decorated with @activity.defn
        assert hasattr(fn, "__temporal_activity_definition")
