"""Tests for alert rules and trigger rules API routes.

Requires Postgres. Skips if not available.
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


def _unique(prefix: str = "r") -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def _seed_user(email: str, password: str, role: str = "admin") -> None:
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


@pytest.fixture()
def client():
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _get_token(client, role="admin") -> str:
    email = _unique(role) + "@test.com"
    _seed_user(email, "pw", role)
    resp = client.post("/api/auth/login", json={"email": email, "password": "pw"})
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _rule_payload(name: str | None = None) -> dict:
    return {
        "name": name or _unique("rule"),
        "description": "Test alert rule",
        "rule_type": "prometheus",
        "rule_definition": {"expr": "rate(errors[5m]) > 0.05", "for": "5m"},
        "severity": "high",
        "is_active": True,
    }


# ===================================================================
# ALERT RULES CRUD
# ===================================================================


class TestCreateAlertRule:
    def test_create_success(self, client):
        token = _get_token(client, "editor")
        payload = _rule_payload()
        resp = client.post("/api/rules", json=payload, headers=_auth(token))
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == payload["name"]
        assert data["rule_type"] == "prometheus"
        assert data["severity"] == "high"
        assert "id" in data

    def test_create_viewer_forbidden(self, client):
        token = _get_token(client, "viewer")
        resp = client.post("/api/rules", json=_rule_payload(), headers=_auth(token))
        assert resp.status_code == 403


class TestListAlertRules:
    def test_list_returns_rules(self, client):
        token = _get_token(client, "editor")
        name = _unique("list")
        client.post("/api/rules", json=_rule_payload(name), headers=_auth(token))
        resp = client.get("/api/rules", headers=_auth(token))
        assert resp.status_code == 200
        names = [r["name"] for r in resp.json()]
        assert name in names

    def test_list_active_only(self, client):
        token = _get_token(client, "admin")
        name = _unique("active")
        payload = _rule_payload(name)
        payload["is_active"] = False
        client.post("/api/rules", json=payload, headers=_auth(token))

        # active_only=true should exclude it
        resp = client.get("/api/rules?active_only=true", headers=_auth(token))
        names = [r["name"] for r in resp.json()]
        assert name not in names


class TestUpdateAlertRule:
    def test_update_success(self, client):
        token = _get_token(client, "editor")
        rule_id = client.post("/api/rules", json=_rule_payload(), headers=_auth(token)).json()["id"]
        resp = client.put(
            f"/api/rules/{rule_id}",
            json={"severity": "critical"},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.json()["severity"] == "critical"

    def test_update_nonexistent_404(self, client):
        token = _get_token(client, "editor")
        resp = client.put(
            f"/api/rules/{uuid4()}",
            json={"severity": "low"},
            headers=_auth(token),
        )
        assert resp.status_code == 404


class TestDeleteAlertRule:
    def test_delete_success(self, client):
        token = _get_token(client, "admin")
        rule_id = client.post("/api/rules", json=_rule_payload(), headers=_auth(token)).json()["id"]
        resp = client.delete(f"/api/rules/{rule_id}", headers=_auth(token))
        assert resp.status_code == 204

    def test_delete_nonexistent_404(self, client):
        token = _get_token(client, "admin")
        resp = client.delete(f"/api/rules/{uuid4()}", headers=_auth(token))
        assert resp.status_code == 404

    def test_delete_non_admin_forbidden(self, client):
        token = _get_token(client, "editor")
        resp = client.delete(f"/api/rules/{uuid4()}", headers=_auth(token))
        assert resp.status_code == 403


# ===================================================================
# TRIGGER RULES
# ===================================================================


class TestListTriggerRules:
    def test_list_trigger_rules(self, client):
        token = _get_token(client)
        resp = client.get("/api/rules/trigger-rules", headers=_auth(token))
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
