"""Tests for app.stores.postgres_store — CRUD operations against Postgres.

Requires a running Postgres instance (from docker-compose.infra.yml).
Tests are skipped automatically if Postgres is not reachable.

To run:  docker compose -f infra/docker-compose.infra.yml up -d postgres
         cd backend && conda run -n mlops_project pytest tests/test_stores/ -v
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio

try:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from app.models.db import Base
    from app.stores import postgres_store as store
    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False

TEST_DB_URL = "postgresql+asyncpg://automend:automend@localhost:5432/automend"


def _pg_available() -> bool:
    """Check if Postgres is reachable."""
    if not _HAS_DEPS:
        return False
    import asyncpg
    try:
        conn = asyncio.get_event_loop().run_until_complete(
            asyncpg.connect(
                user="automend", password="automend",
                database="automend", host="localhost", port=5432,
                timeout=3,
            )
        )
        asyncio.get_event_loop().run_until_complete(conn.close())
        return True
    except Exception:
        return False


_pg_is_up = _pg_available()
pytestmark = pytest.mark.skipif(not _pg_is_up, reason="Postgres not available")


@pytest_asyncio.fixture()
async def session():
    """Create a fresh engine+session per test, rollback at the end."""
    import sqlalchemy as sa

    eng = create_async_engine(TEST_DB_URL, echo=False)
    # Ensure extensions + tables exist (idempotent)
    async with eng.begin() as conn:
        await conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(sa.text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        await conn.run_sync(Base.metadata.create_all)

    async with eng.connect() as conn:
        txn = await conn.begin()
        sess = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield sess
        finally:
            await sess.close()
            await txn.rollback()

    await eng.dispose()


# ===================================================================
# TOOLS
# ===================================================================


class TestToolsCRUD:
    async def test_create_and_get(self, session):
        tool = await store.create_tool(
            session,
            name=f"test_tool_{uuid4().hex[:8]}",
            display_name="Test Tool",
            description="A test tool",
            category="testing",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        assert tool.id is not None
        fetched = await store.get_tool(session, tool.id)
        assert fetched is not None
        assert fetched.name == tool.name
        assert fetched.embedding_text is not None

    async def test_get_by_name(self, session):
        name = f"named_tool_{uuid4().hex[:8]}"
        await store.create_tool(
            session, name=name, display_name="Named", description="D",
            category="c", input_schema={}, output_schema={},
        )
        fetched = await store.get_tool_by_name(session, name)
        assert fetched is not None
        assert fetched.name == name

    async def test_list_tools(self, session):
        name = f"list_tool_{uuid4().hex[:8]}"
        await store.create_tool(
            session, name=name, display_name="L", description="D",
            category="kubernetes", input_schema={}, output_schema={},
        )
        tools = await store.list_tools(session)
        assert any(t.name == name for t in tools)

    async def test_list_tools_by_category(self, session):
        cat = f"cat_{uuid4().hex[:8]}"
        await store.create_tool(
            session, name=f"tool_{uuid4().hex[:8]}", display_name="X",
            description="D", category=cat, input_schema={}, output_schema={},
        )
        tools = await store.list_tools(session, category=cat)
        assert len(tools) == 1
        assert tools[0].category == cat

    async def test_update_tool(self, session):
        tool = await store.create_tool(
            session, name=f"upd_{uuid4().hex[:8]}", display_name="Old",
            description="D", category="c", input_schema={}, output_schema={},
        )
        updated = await store.update_tool(session, tool.id, display_name="New")
        assert updated.display_name == "New"

    async def test_deactivate_tool(self, session):
        tool = await store.create_tool(
            session, name=f"deact_{uuid4().hex[:8]}", display_name="X",
            description="D", category="c", input_schema={}, output_schema={},
        )
        assert await store.deactivate_tool(session, tool.id) is True
        fetched = await store.get_tool(session, tool.id)
        assert fetched.is_active is False

    async def test_get_nonexistent_returns_none(self, session):
        assert await store.get_tool(session, uuid4()) is None

    async def test_deactivate_nonexistent_returns_false(self, session):
        assert await store.deactivate_tool(session, uuid4()) is False


# ===================================================================
# PLAYBOOKS + VERSIONS
# ===================================================================


class TestPlaybooksCRUD:
    async def test_create_and_get(self, session):
        pb = await store.create_playbook(
            session, name="GPU Recovery", description="Handles GPU OOM"
        )
        assert pb.id is not None
        fetched = await store.get_playbook(session, pb.id)
        assert fetched.name == "GPU Recovery"

    async def test_list_playbooks(self, session):
        await store.create_playbook(session, name=f"pb_{uuid4().hex[:8]}")
        pbs = await store.list_playbooks(session)
        assert len(pbs) >= 1

    async def test_delete_playbook(self, session):
        pb = await store.create_playbook(session, name=f"del_{uuid4().hex[:8]}")
        assert await store.delete_playbook(session, pb.id) is True
        assert await store.get_playbook(session, pb.id) is None

    async def test_delete_nonexistent_returns_false(self, session):
        assert await store.delete_playbook(session, uuid4()) is False


class TestPlaybookVersionsCRUD:
    async def test_save_and_get_version(self, session):
        pb = await store.create_playbook(session, name=f"pb_{uuid4().hex[:8]}")
        v = await store.save_version(
            session, pb.id,
            workflow_spec={"name": "test", "version": "1.0.0", "trigger": {}, "steps": []},
            change_notes="Initial",
        )
        assert v.version_number == 1
        assert v.spec_checksum is not None
        fetched = await store.get_version(session, v.id)
        assert fetched.workflow_spec["name"] == "test"

    async def test_auto_increment_version(self, session):
        pb = await store.create_playbook(session, name=f"pb_{uuid4().hex[:8]}")
        v1 = await store.save_version(session, pb.id, workflow_spec={"v": 1})
        v2 = await store.save_version(session, pb.id, workflow_spec={"v": 2})
        assert v1.version_number == 1
        assert v2.version_number == 2

    async def test_list_versions(self, session):
        pb = await store.create_playbook(session, name=f"pb_{uuid4().hex[:8]}")
        await store.save_version(session, pb.id, workflow_spec={"v": 1})
        await store.save_version(session, pb.id, workflow_spec={"v": 2})
        versions = await store.get_versions(session, pb.id)
        assert len(versions) == 2
        # Newest first
        assert versions[0].version_number == 2

    async def test_transition_status(self, session):
        pb = await store.create_playbook(session, name=f"pb_{uuid4().hex[:8]}")
        v = await store.save_version(session, pb.id, workflow_spec={"v": 1})
        assert v.status == "draft"
        updated = await store.transition_version_status(session, v.id, "validated")
        assert updated.status == "validated"


# ===================================================================
# TRIGGER RULES
# ===================================================================


class TestTriggerRulesCRUD:
    async def test_create_and_find(self, session):
        pb = await store.create_playbook(session, name=f"pb_{uuid4().hex[:8]}")
        v = await store.save_version(session, pb.id, workflow_spec={"v": 1})
        rule = await store.create_trigger_rule(
            session,
            incident_type="incident.gpu_memory_failure",
            playbook_version_id=v.id,
            priority=10,
        )
        assert rule.id is not None
        found = await store.find_playbook_for_incident(
            session, "incident.gpu_memory_failure"
        )
        assert found is not None
        assert found.playbook_version_id == v.id

    async def test_find_returns_highest_priority(self, session):
        pb = await store.create_playbook(session, name=f"pb_{uuid4().hex[:8]}")
        v1 = await store.save_version(session, pb.id, workflow_spec={"v": 1})
        v2 = await store.save_version(session, pb.id, workflow_spec={"v": 2})
        itype = f"incident.test_{uuid4().hex[:8]}"
        await store.create_trigger_rule(
            session, incident_type=itype, playbook_version_id=v1.id, priority=1
        )
        await store.create_trigger_rule(
            session, incident_type=itype, playbook_version_id=v2.id, priority=10
        )
        found = await store.find_playbook_for_incident(session, itype)
        assert found.playbook_version_id == v2.id

    async def test_find_no_match(self, session):
        found = await store.find_playbook_for_incident(session, "incident.nonexistent")
        assert found is None

    async def test_deactivate(self, session):
        pb = await store.create_playbook(session, name=f"pb_{uuid4().hex[:8]}")
        v = await store.save_version(session, pb.id, workflow_spec={"v": 1})
        rule = await store.create_trigger_rule(
            session, incident_type="incident.x", playbook_version_id=v.id,
        )
        assert await store.deactivate_trigger_rule(session, rule.id) is True


# ===================================================================
# INCIDENTS + EVENTS
# ===================================================================


class TestIncidentsCRUD:
    async def _make_incident(self, session, **kwargs):
        defaults = dict(
            incident_key=f"prod/ml/trainer/{uuid4().hex[:8]}",
            incident_type="incident.gpu_memory_failure",
            severity="high",
            entity={"cluster": "prod-a", "namespace": "ml", "service": "trainer"},
            sources=["log_classifier"],
            evidence={"metric_alerts": [], "raw_signals": []},
        )
        defaults.update(kwargs)
        return await store.create_incident(session, **defaults)

    async def test_create_and_get(self, session):
        inc = await self._make_incident(session)
        assert inc.status == "open"
        fetched = await store.get_incident(session, inc.id)
        assert fetched.incident_type == "incident.gpu_memory_failure"

    async def test_get_by_key(self, session):
        key = f"prod/ml/trainer/{uuid4().hex[:8]}"
        await self._make_incident(session, incident_key=key)
        fetched = await store.get_incident_by_key(session, key)
        assert fetched is not None
        assert fetched.incident_key == key

    async def test_list_with_filters(self, session):
        await self._make_incident(session, severity="critical")
        incidents = await store.list_incidents(session, severity="critical")
        assert len(incidents) >= 1
        assert all(i.severity == "critical" for i in incidents)

    async def test_update_incident(self, session):
        inc = await self._make_incident(session)
        updated = await store.update_incident(session, inc.id, status="acknowledged")
        assert updated.status == "acknowledged"

    async def test_resolve_incident(self, session):
        inc = await self._make_incident(session)
        resolved = await store.resolve_incident(session, inc.id)
        assert resolved.status == "resolved"
        assert resolved.resolved_at is not None

    async def test_get_stats(self, session):
        await self._make_incident(session)
        stats = await store.get_incident_stats(session)
        assert "by_status" in stats
        assert "by_severity" in stats

    async def test_update_nonexistent_returns_none(self, session):
        assert await store.update_incident(session, uuid4(), status="x") is None


class TestIncidentEventsCRUD:
    async def test_add_and_list_events(self, session):
        inc = await store.create_incident(
            session,
            incident_key=f"prod/ml/{uuid4().hex[:8]}",
            incident_type="incident.test",
            severity="medium",
            entity={"cluster": "prod"},
            sources=["test"],
            evidence={},
        )
        e1 = await store.add_event(session, inc.id, "created", {"note": "created"})
        e2 = await store.add_event(session, inc.id, "signal_added", {"signal": "x"})
        events = await store.get_incident_events(session, inc.id)
        assert len(events) == 2
        assert events[0].event_type == "created"
        assert events[1].event_type == "signal_added"


# ===================================================================
# APPROVAL REQUESTS
# ===================================================================


class TestApprovalRequestsCRUD:
    async def test_create_get_decide(self, session):
        inc = await store.create_incident(
            session,
            incident_key=f"prod/ml/{uuid4().hex[:8]}",
            incident_type="incident.test",
            severity="medium",
            entity={},
            sources=["test"],
            evidence={},
        )
        ar = await store.create_approval_request(
            session,
            incident_id=inc.id,
            workflow_id="wf-123",
            step_name="restart",
            requested_action="Restart trainer pod",
            requested_by="system",
        )
        assert ar.status == "pending"

        decided = await store.decide_approval(
            session, ar.id, "approved", "admin@example.com", notes="LGTM"
        )
        assert decided.status == "approved"
        assert decided.decided_by == "admin@example.com"
        assert decided.decided_at is not None


# ===================================================================
# ALERT RULES
# ===================================================================


class TestAlertRulesCRUD:
    async def test_create_and_get(self, session):
        rule = await store.create_alert_rule(
            session,
            name=f"High Error Rate {uuid4().hex[:8]}",
            rule_type="prometheus",
            rule_definition={"expr": "rate(errors[5m]) > 0.05"},
        )
        fetched = await store.get_alert_rule(session, rule.id)
        assert fetched.name == rule.name

    async def test_list_rules(self, session):
        await store.create_alert_rule(
            session, name=f"Rule {uuid4().hex[:8]}",
            rule_type="prometheus", rule_definition={},
        )
        rules = await store.list_alert_rules(session)
        assert len(rules) >= 1

    async def test_update_rule(self, session):
        rule = await store.create_alert_rule(
            session, name=f"Rule {uuid4().hex[:8]}",
            rule_type="prometheus", rule_definition={},
        )
        updated = await store.update_alert_rule(session, rule.id, severity="critical")
        assert updated.severity == "critical"

    async def test_delete_rule(self, session):
        rule = await store.create_alert_rule(
            session, name=f"Rule {uuid4().hex[:8]}",
            rule_type="prometheus", rule_definition={},
        )
        assert await store.delete_alert_rule(session, rule.id) is True
        assert await store.get_alert_rule(session, rule.id) is None


# ===================================================================
# USERS
# ===================================================================


class TestUsersCRUD:
    async def test_create_and_get_by_email(self, session):
        email = f"test_{uuid4().hex[:8]}@example.com"
        user = await store.create_user(
            session, email=email, hashed_password="hashed", role="admin"
        )
        fetched = await store.get_user_by_email(session, email)
        assert fetched is not None
        assert fetched.role == "admin"

    async def test_get_user_by_id(self, session):
        email = f"id_{uuid4().hex[:8]}@example.com"
        user = await store.create_user(session, email=email, role="viewer")
        fetched = await store.get_user(session, user.id)
        assert fetched.email == email

    async def test_get_nonexistent_email_returns_none(self, session):
        assert await store.get_user_by_email(session, "nope@x.com") is None
