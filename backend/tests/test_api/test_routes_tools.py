"""Tests for tools API routes: list, get, create, update, delete.

Requires Postgres. Skips if not available.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

try:
    import psycopg2
    from passlib.context import CryptContext
    from fastapi.testclient import TestClient

    from app.config import get_settings
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


def _unique(prefix: str = "t") -> str:
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


@pytest.fixture()
def admin_token(client) -> str:
    email = _unique("admin") + "@test.com"
    _seed_user(email, "pw", "admin")
    resp = client.post("/api/auth/login", json={"email": email, "password": "pw"})
    return resp.json()["access_token"]


@pytest.fixture()
def viewer_token(client) -> str:
    email = _unique("viewer") + "@test.com"
    _seed_user(email, "pw", "viewer")
    resp = client.post("/api/auth/login", json={"email": email, "password": "pw"})
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _tool_payload(name: str | None = None) -> dict:
    return {
        "name": name or _unique("tool"),
        "display_name": "Test Tool",
        "description": "A tool for testing",
        "category": "kubernetes",
        "input_schema": {"type": "object", "properties": {"ns": {"type": "string"}}, "required": ["ns"]},
        "output_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        "side_effect_level": "read",
        "required_approvals": 0,
        "environments_allowed": ["production", "staging", "development"],
    }


# ===================================================================
# CREATE
# ===================================================================


class TestCreateTool:
    def test_create_success(self, client, admin_token):
        payload = _tool_payload()
        resp = client.post("/api/tools", json=payload, headers=_auth(admin_token))
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == payload["name"]
        assert data["category"] == "kubernetes"
        assert "id" in data
        assert "created_at" in data

    def test_create_duplicate_name_409(self, client, admin_token):
        name = _unique("dup")
        client.post("/api/tools", json=_tool_payload(name), headers=_auth(admin_token))
        resp = client.post("/api/tools", json=_tool_payload(name), headers=_auth(admin_token))
        assert resp.status_code == 409

    def test_create_non_admin_forbidden(self, client, viewer_token):
        resp = client.post("/api/tools", json=_tool_payload(), headers=_auth(viewer_token))
        assert resp.status_code == 403

    def test_create_no_auth_403(self, client):
        resp = client.post("/api/tools", json=_tool_payload())
        assert resp.status_code == 403


# ===================================================================
# LIST
# ===================================================================


class TestListTools:
    def test_list_returns_tools(self, client, admin_token):
        name = _unique("list")
        client.post("/api/tools", json=_tool_payload(name), headers=_auth(admin_token))
        resp = client.get("/api/tools", headers=_auth(admin_token))
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()]
        assert name in names

    def test_list_filter_by_category(self, client, admin_token):
        cat = _unique("cat")
        payload = _tool_payload()
        payload["category"] = cat
        client.post("/api/tools", json=payload, headers=_auth(admin_token))
        resp = client.get(f"/api/tools?category={cat}", headers=_auth(admin_token))
        assert resp.status_code == 200
        tools = resp.json()
        assert len(tools) >= 1
        assert all(t["category"] == cat for t in tools)

    def test_list_active_only_excludes_deactivated(self, client, admin_token):
        name = _unique("deact")
        create_resp = client.post("/api/tools", json=_tool_payload(name), headers=_auth(admin_token))
        tool_id = create_resp.json()["id"]
        client.delete(f"/api/tools/{tool_id}", headers=_auth(admin_token))

        resp = client.get("/api/tools?active_only=true", headers=_auth(admin_token))
        names = [t["name"] for t in resp.json()]
        assert name not in names

        resp2 = client.get("/api/tools?active_only=false", headers=_auth(admin_token))
        names2 = [t["name"] for t in resp2.json()]
        assert name in names2


# ===================================================================
# GET
# ===================================================================


class TestGetTool:
    def test_get_by_id(self, client, admin_token):
        create_resp = client.post("/api/tools", json=_tool_payload(), headers=_auth(admin_token))
        tool_id = create_resp.json()["id"]
        resp = client.get(f"/api/tools/{tool_id}", headers=_auth(admin_token))
        assert resp.status_code == 200
        assert resp.json()["id"] == tool_id

    def test_get_nonexistent_404(self, client, admin_token):
        resp = client.get(f"/api/tools/{uuid4()}", headers=_auth(admin_token))
        assert resp.status_code == 404


# ===================================================================
# UPDATE
# ===================================================================


class TestUpdateTool:
    def test_update_fields(self, client, admin_token):
        create_resp = client.post("/api/tools", json=_tool_payload(), headers=_auth(admin_token))
        tool_id = create_resp.json()["id"]
        resp = client.put(
            f"/api/tools/{tool_id}",
            json={"display_name": "Updated Name", "side_effect_level": "write"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Updated Name"
        assert resp.json()["side_effect_level"] == "write"

    def test_update_nonexistent_404(self, client, admin_token):
        resp = client.put(
            f"/api/tools/{uuid4()}",
            json={"display_name": "X"},
            headers=_auth(admin_token),
        )
        assert resp.status_code == 404

    def test_update_non_admin_forbidden(self, client, viewer_token):
        resp = client.put(
            f"/api/tools/{uuid4()}",
            json={"display_name": "X"},
            headers=_auth(viewer_token),
        )
        assert resp.status_code == 403


# ===================================================================
# DELETE (deactivate)
# ===================================================================


class TestDeleteTool:
    def test_delete_deactivates(self, client, admin_token):
        create_resp = client.post("/api/tools", json=_tool_payload(), headers=_auth(admin_token))
        tool_id = create_resp.json()["id"]
        resp = client.delete(f"/api/tools/{tool_id}", headers=_auth(admin_token))
        assert resp.status_code == 204

        # Verify it's deactivated (still gettable but is_active=false)
        get_resp = client.get(f"/api/tools/{tool_id}", headers=_auth(admin_token))
        assert get_resp.json()["is_active"] is False

    def test_delete_nonexistent_404(self, client, admin_token):
        resp = client.delete(f"/api/tools/{uuid4()}", headers=_auth(admin_token))
        assert resp.status_code == 404

    def test_delete_non_admin_forbidden(self, client, viewer_token):
        resp = client.delete(f"/api/tools/{uuid4()}", headers=_auth(viewer_token))
        assert resp.status_code == 403
