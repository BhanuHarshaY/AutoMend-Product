"""Tests for seed data and seed scripts.

Data validation tests run without a database.
Integration tests require Postgres and are skipped if unavailable.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio

from scripts.seed_data import DEFAULT_ALERT_RULES, DEFAULT_TOOLS

# ---------------------------------------------------------------------------
# Seed data validation (no DB required)
# ---------------------------------------------------------------------------

REQUIRED_TOOL_FIELDS = [
    "name", "display_name", "description", "category",
    "input_schema", "output_schema", "side_effect_level",
    "required_approvals", "environments_allowed",
]

VALID_SIDE_EFFECTS = {"read", "write", "destructive"}
VALID_CATEGORIES = {"kubernetes", "observability", "notification", "ticketing"}

EXPECTED_TOOL_NAMES = [
    "fetch_pod_logs", "query_prometheus", "restart_workload",
    "scale_deployment", "rollback_release", "page_oncall",
    "slack_notification", "slack_approval", "open_ticket",
    "describe_pod", "get_node_status", "cordon_node",
    "drain_node", "run_diagnostic_script",
]


class TestDefaultToolsData:
    def test_tool_count(self):
        assert len(DEFAULT_TOOLS) == 14

    def test_all_expected_tools_present(self):
        names = [t["name"] for t in DEFAULT_TOOLS]
        for expected in EXPECTED_TOOL_NAMES:
            assert expected in names, f"Missing tool: {expected}"

    def test_unique_names(self):
        names = [t["name"] for t in DEFAULT_TOOLS]
        assert len(names) == len(set(names)), "Duplicate tool names found"

    @pytest.mark.parametrize("tool", DEFAULT_TOOLS, ids=lambda t: t["name"])
    def test_required_fields(self, tool):
        for field in REQUIRED_TOOL_FIELDS:
            assert field in tool, f"Tool '{tool['name']}' missing field '{field}'"

    @pytest.mark.parametrize("tool", DEFAULT_TOOLS, ids=lambda t: t["name"])
    def test_side_effect_level_valid(self, tool):
        assert tool["side_effect_level"] in VALID_SIDE_EFFECTS

    @pytest.mark.parametrize("tool", DEFAULT_TOOLS, ids=lambda t: t["name"])
    def test_category_valid(self, tool):
        assert tool["category"] in VALID_CATEGORIES

    @pytest.mark.parametrize("tool", DEFAULT_TOOLS, ids=lambda t: t["name"])
    def test_input_schema_has_type(self, tool):
        assert tool["input_schema"].get("type") == "object"

    @pytest.mark.parametrize("tool", DEFAULT_TOOLS, ids=lambda t: t["name"])
    def test_output_schema_has_type(self, tool):
        assert tool["output_schema"].get("type") == "object"

    @pytest.mark.parametrize("tool", DEFAULT_TOOLS, ids=lambda t: t["name"])
    def test_environments_allowed_nonempty(self, tool):
        assert len(tool["environments_allowed"]) > 0

    def test_destructive_tools_require_approval(self):
        for tool in DEFAULT_TOOLS:
            if tool["side_effect_level"] == "destructive":
                assert tool["required_approvals"] >= 1, (
                    f"Destructive tool '{tool['name']}' should require approval"
                )


class TestDefaultAlertRulesData:
    def test_rule_count(self):
        assert len(DEFAULT_ALERT_RULES) == 5

    def test_unique_names(self):
        names = [r["name"] for r in DEFAULT_ALERT_RULES]
        assert len(names) == len(set(names))

    @pytest.mark.parametrize("rule", DEFAULT_ALERT_RULES, ids=lambda r: r["name"])
    def test_required_fields(self, rule):
        assert "name" in rule
        assert "rule_type" in rule
        assert "rule_definition" in rule
        assert "severity" in rule

    @pytest.mark.parametrize("rule", DEFAULT_ALERT_RULES, ids=lambda r: r["name"])
    def test_rule_type_is_prometheus(self, rule):
        assert rule["rule_type"] == "prometheus"

    @pytest.mark.parametrize("rule", DEFAULT_ALERT_RULES, ids=lambda r: r["name"])
    def test_definition_has_expr(self, rule):
        assert "expr" in rule["rule_definition"]

    @pytest.mark.parametrize("rule", DEFAULT_ALERT_RULES, ids=lambda r: r["name"])
    def test_definition_has_incident_type(self, rule):
        assert "incident_type" in rule["rule_definition"]


# ---------------------------------------------------------------------------
# Integration tests (require Postgres)
# ---------------------------------------------------------------------------

try:
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from app.models.db import Base
    from app.stores import postgres_store as store
    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False

TEST_DB_URL = "postgresql+asyncpg://automend:automend@localhost:5432/automend"


def _pg_available() -> bool:
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


@pytest_asyncio.fixture()
async def session():
    if not _pg_is_up:
        pytest.skip("Postgres not available")
    import sqlalchemy as sa
    eng = create_async_engine(TEST_DB_URL, echo=False)
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


class TestSeedToolsIntegration:
    @pytest.mark.skipif(not _pg_is_up, reason="Postgres not available")
    async def test_seed_all_tools(self, session):
        """Seed all tools and verify they're in the DB."""
        for tool_data in DEFAULT_TOOLS:
            existing = await store.get_tool_by_name(session, tool_data["name"])
            if existing is None:
                await store.create_tool(session, **tool_data)

        tools = await store.list_tools(session, active_only=False)
        seeded_names = {t.name for t in tools}
        for expected in EXPECTED_TOOL_NAMES:
            assert expected in seeded_names

    @pytest.mark.skipif(not _pg_is_up, reason="Postgres not available")
    async def test_idempotent_seed(self, session):
        """Seeding twice should not create duplicates."""
        # First pass — skip if already exists (DB may have data from other tests)
        for tool_data in DEFAULT_TOOLS:
            existing = await store.get_tool_by_name(session, tool_data["name"])
            if existing is None:
                await store.create_tool(session, **tool_data)

        # Second pass — skip existing
        for tool_data in DEFAULT_TOOLS:
            existing = await store.get_tool_by_name(session, tool_data["name"])
            if existing is None:
                await store.create_tool(session, **tool_data)

        tools = await store.list_tools(session, active_only=False)
        names = [t.name for t in tools]
        # No duplicates
        assert len(names) == len(set(names))


class TestSeedRulesIntegration:
    @pytest.mark.skipif(not _pg_is_up, reason="Postgres not available")
    async def test_seed_all_rules(self, session):
        for rule_data in DEFAULT_ALERT_RULES:
            await store.create_alert_rule(session, **rule_data)

        rules = await store.list_alert_rules(session)
        assert len(rules) >= len(DEFAULT_ALERT_RULES)
