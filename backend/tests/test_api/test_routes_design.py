"""Tests for design plane API routes (§13).

Validate endpoint tested with seeded tools in Postgres.
RAG search tested as integration (returns empty with zero-vector embeddings).
Generate tested with mock architect.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

try:
    import psycopg2
    from passlib.context import CryptContext
    from fastapi.testclient import TestClient

    from main_api import create_app

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False


def _pg_available() -> bool:
    if not _HAS_DEPS:
        return False
    try:
        conn = psycopg2.connect(
            dbname="automend", user="automend", password="automend",
            host="localhost", port=5432, connect_timeout=3,
        )
        conn.close()
        return True
    except Exception:
        return False


_pg_is_up = _pg_available()
pytestmark = pytest.mark.skipif(not _pg_is_up, reason="Postgres not available")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _unique(prefix: str = "d") -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def _seed_user(email: str, password: str, role: str = "editor") -> None:
    conn = psycopg2.connect(
        dbname="automend", user="automend", password="automend",
        host="localhost", port=5432,
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (id, email, hashed_password, role, is_active, created_at) "
        "VALUES (gen_random_uuid(), %s, %s, %s, true, now()) ON CONFLICT (email) DO NOTHING",
        (email, pwd_context.hash(password), role),
    )
    cur.close()
    conn.close()


def _seed_tool(name: str, side_effect_level: str = "read") -> None:
    """Seed a tool directly via psycopg2."""
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
        "VALUES (gen_random_uuid(), %s, %s, %s, 'kubernetes', "
        "'{}'::jsonb, '{}'::jsonb, %s, 0, "
        "'{production,staging,development}', %s, true, now(), now()) "
        "ON CONFLICT (name) DO NOTHING",
        (name, name.replace("_", " ").title(), f"Tool {name}", side_effect_level, name),
    )
    cur.close()
    conn.close()


@pytest.fixture()
def client():
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _get_token(client, role="editor") -> str:
    email = _unique(role) + "@test.com"
    _seed_user(email, "pw", role)
    resp = client.post("/api/auth/login", json={"email": email, "password": "pw"})
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ===================================================================
# VALIDATE WORKFLOW
# ===================================================================


class TestValidateWorkflow:
    def test_valid_spec(self, client):
        token = _get_token(client)
        tool_name = _unique("tool")
        _seed_tool(tool_name)

        spec = {
            "name": "Test Playbook",
            "version": "1.0.0",
            "trigger": {"incident_types": ["incident.test"]},
            "steps": [
                {"id": "step1", "name": "Fetch", "type": "action", "tool": tool_name,
                 "input": {"ns": "ml"}},
            ],
        }
        resp = client.post("/api/design/validate_workflow", json={"workflow_spec": spec}, headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["errors"] == []

    def test_missing_required_fields(self, client):
        token = _get_token(client)
        resp = client.post(
            "/api/design/validate_workflow",
            json={"workflow_spec": {"description": "incomplete"}},
            headers=_auth(token),
        )
        data = resp.json()
        assert data["valid"] is False
        assert any("name" in e for e in data["errors"])
        assert any("trigger" in e for e in data["errors"])
        assert any("steps" in e for e in data["errors"])

    def test_unknown_tool_error(self, client):
        token = _get_token(client)
        spec = {
            "name": "Test", "version": "1.0.0",
            "trigger": {"incident_types": ["incident.test"]},
            "steps": [{"id": "s1", "name": "S1", "type": "action", "tool": "nonexistent_tool_xyz"}],
        }
        resp = client.post("/api/design/validate_workflow", json={"workflow_spec": spec}, headers=_auth(token))
        data = resp.json()
        assert data["valid"] is False
        assert any("nonexistent_tool_xyz" in e for e in data["errors"])

    def test_duplicate_step_ids(self, client):
        token = _get_token(client)
        tool_name = _unique("tool")
        _seed_tool(tool_name)
        spec = {
            "name": "Test", "version": "1.0.0",
            "trigger": {"incident_types": ["incident.test"]},
            "steps": [
                {"id": "dup", "name": "A", "type": "action", "tool": tool_name},
                {"id": "dup", "name": "B", "type": "action", "tool": tool_name},
            ],
        }
        resp = client.post("/api/design/validate_workflow", json={"workflow_spec": spec}, headers=_auth(token))
        data = resp.json()
        assert data["valid"] is False
        assert any("Duplicate" in e for e in data["errors"])

    def test_invalid_step_type(self, client):
        token = _get_token(client)
        spec = {
            "name": "Test", "version": "1.0.0",
            "trigger": {"incident_types": ["incident.test"]},
            "steps": [{"id": "s1", "name": "S1", "type": "invalid_type"}],
        }
        resp = client.post("/api/design/validate_workflow", json={"workflow_spec": spec}, headers=_auth(token))
        data = resp.json()
        assert data["valid"] is False
        assert any("invalid type" in e for e in data["errors"])

    def test_missing_trigger_incident_types(self, client):
        token = _get_token(client)
        spec = {
            "name": "Test", "version": "1.0.0",
            "trigger": {},
            "steps": [],
        }
        resp = client.post("/api/design/validate_workflow", json={"workflow_spec": spec}, headers=_auth(token))
        data = resp.json()
        assert data["valid"] is False
        assert any("incident_types" in e for e in data["errors"])

    def test_referenced_step_not_found(self, client):
        token = _get_token(client)
        tool_name = _unique("tool")
        _seed_tool(tool_name)
        spec = {
            "name": "Test", "version": "1.0.0",
            "trigger": {"incident_types": ["incident.test"]},
            "steps": [
                {"id": "s1", "name": "S1", "type": "action", "tool": tool_name, "on_failure": "nonexistent_step"},
            ],
        }
        resp = client.post("/api/design/validate_workflow", json={"workflow_spec": spec}, headers=_auth(token))
        data = resp.json()
        assert data["valid"] is False
        assert any("nonexistent_step" in e for e in data["errors"])

    def test_write_tool_without_approval_warning(self, client):
        token = _get_token(client)
        tool_name = _unique("write_tool")
        _seed_tool(tool_name, side_effect_level="write")
        spec = {
            "name": "Test", "version": "1.0.0",
            "trigger": {"incident_types": ["incident.test"]},
            "steps": [
                {"id": "s1", "name": "Restart", "type": "action", "tool": tool_name},
            ],
        }
        resp = client.post("/api/design/validate_workflow", json={"workflow_spec": spec}, headers=_auth(token))
        data = resp.json()
        assert data["valid"] is True  # Warnings don't invalidate
        assert any("approval" in w.lower() for w in data["warnings"])

    def test_condition_missing_expression(self, client):
        token = _get_token(client)
        spec = {
            "name": "Test", "version": "1.0.0",
            "trigger": {"incident_types": ["incident.test"]},
            "steps": [{"id": "c1", "name": "Check", "type": "condition"}],
        }
        resp = client.post("/api/design/validate_workflow", json={"workflow_spec": spec}, headers=_auth(token))
        data = resp.json()
        assert any("condition" in e.lower() and "expression" in e.lower() for e in data["errors"])

    def test_delay_missing_duration(self, client):
        token = _get_token(client)
        spec = {
            "name": "Test", "version": "1.0.0",
            "trigger": {"incident_types": ["incident.test"]},
            "steps": [{"id": "w1", "name": "Wait", "type": "delay"}],
        }
        resp = client.post("/api/design/validate_workflow", json={"workflow_spec": spec}, headers=_auth(token))
        data = resp.json()
        assert any("duration" in e for e in data["errors"])

    def test_empty_steps_warning(self, client):
        token = _get_token(client)
        spec = {
            "name": "Test", "version": "1.0.0",
            "trigger": {"incident_types": ["incident.test"]},
            "steps": [],
        }
        resp = client.post("/api/design/validate_workflow", json={"workflow_spec": spec}, headers=_auth(token))
        data = resp.json()
        assert data["valid"] is True
        assert any("no steps" in w.lower() for w in data["warnings"])


# ===================================================================
# RAG SEARCH
# ===================================================================


class TestRAGSearch:
    def test_rag_search_returns_structure(self, client):
        token = _get_token(client)
        resp = client.post(
            "/api/design/rag_search",
            json={"query": "restart a crashed pod", "search_types": ["tools", "playbooks"]},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "tools" in data
        assert "playbooks" in data
        assert isinstance(data["tools"], list)
        assert isinstance(data["playbooks"], list)

    def test_rag_search_requires_auth(self, client):
        resp = client.post("/api/design/rag_search", json={"query": "test"})
        assert resp.status_code == 403


# ===================================================================
# GENERATE (basic structure test — no real architect call)
# ===================================================================


class TestGenerateWorkflow:
    def test_generate_requires_editor(self, client):
        token = _get_token(client, "viewer")
        resp = client.post(
            "/api/design/generate_workflow",
            json={"intent": "restart a crashed pod"},
            headers=_auth(token),
        )
        assert resp.status_code == 403

    def test_generate_without_architect_key_returns_502(self, client):
        """Without an architect API key configured, the call should fail gracefully."""
        token = _get_token(client, "editor")
        resp = client.post(
            "/api/design/generate_workflow",
            json={"intent": "restart pods", "target_incident_types": ["incident.test"]},
            headers=_auth(token),
        )
        # Should be 502 (architect service error) since no real API key
        assert resp.status_code == 502
