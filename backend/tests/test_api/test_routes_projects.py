"""Tests for projects API routes.

Updated for Task 11.8c: projects are bound to a unique Kubernetes namespace
and gated by a playbooks_enabled kill switch (replacing the display-only
status enum). Requires Postgres. Skips if not available.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

try:
    import psycopg2
    from fastapi.testclient import TestClient
    from passlib.context import CryptContext

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


def _unique(prefix: str = "p") -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def _unique_ns(prefix: str = "ns") -> str:
    """Namespace-safe slug. DNS-1123: lowercase alphanumerics + hyphens."""
    return f"{prefix}-{uuid4().hex[:8]}"


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


# ===================================================================
# CREATE
# ===================================================================


class TestCreateProject:
    def test_create_success(self, client):
        token = _get_token(client, "editor")
        resp = client.post(
            "/api/projects",
            json={
                "name": _unique("proj"),
                "namespace": _unique_ns("ml"),
                "description": "Classification service",
                "owner_team": "ml-platform",
            },
            headers=_auth(token),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert data["description"] == "Classification service"
        assert data["owner_team"] == "ml-platform"
        assert data["playbooks_enabled"] is True  # default

    def test_create_viewer_forbidden(self, client):
        token = _get_token(client, "viewer")
        resp = client.post(
            "/api/projects",
            json={"name": _unique("proj"), "namespace": _unique_ns()},
            headers=_auth(token),
        )
        assert resp.status_code == 403

    def test_create_requires_auth(self, client):
        resp = client.post("/api/projects", json={"name": "x", "namespace": "x"})
        assert resp.status_code in (401, 403)

    def test_create_missing_namespace_422(self, client):
        """Task 11.8c — namespace is required on create."""
        token = _get_token(client, "editor")
        resp = client.post(
            "/api/projects",
            json={"name": _unique("proj")},
            headers=_auth(token),
        )
        assert resp.status_code == 422

    def test_create_duplicate_namespace_409(self, client):
        """Two projects cannot own the same namespace."""
        token = _get_token(client, "editor")
        ns = _unique_ns("dup")
        first = client.post(
            "/api/projects",
            json={"name": _unique("first"), "namespace": ns},
            headers=_auth(token),
        )
        assert first.status_code == 201

        second = client.post(
            "/api/projects",
            json={"name": _unique("second"), "namespace": ns},
            headers=_auth(token),
        )
        assert second.status_code == 409
        assert "already bound" in second.json()["detail"].lower()


# ===================================================================
# LIST
# ===================================================================


class TestListProjects:
    def test_list_returns_created(self, client):
        token = _get_token(client, "editor")
        name = _unique("list")
        client.post(
            "/api/projects",
            json={"name": name, "namespace": _unique_ns()},
            headers=_auth(token),
        )
        resp = client.get("/api/projects", headers=_auth(token))
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()]
        assert name in names

    def test_list_filter_by_enabled(self, client):
        """Task 11.8c — ?enabled= filters by playbooks_enabled column."""
        admin = _get_token(client, "admin")
        # Create + disable
        disabled = client.post(
            "/api/projects",
            json={"name": _unique("off"), "namespace": _unique_ns("off")},
            headers=_auth(admin),
        ).json()
        client.patch(
            f"/api/projects/{disabled['id']}",
            json={"playbooks_enabled": False},
            headers=_auth(admin),
        )
        # Create + leave enabled (default)
        enabled = client.post(
            "/api/projects",
            json={"name": _unique("on"), "namespace": _unique_ns("on")},
            headers=_auth(admin),
        ).json()

        enabled_resp = client.get("/api/projects?enabled=true", headers=_auth(admin))
        assert enabled_resp.status_code == 200
        enabled_ids = {p["id"] for p in enabled_resp.json()}
        assert enabled["id"] in enabled_ids
        assert disabled["id"] not in enabled_ids

        disabled_resp = client.get("/api/projects?enabled=false", headers=_auth(admin))
        assert disabled_resp.status_code == 200
        disabled_ids = {p["id"] for p in disabled_resp.json()}
        assert disabled["id"] in disabled_ids
        assert enabled["id"] not in disabled_ids


# ===================================================================
# GET (with playbooks)
# ===================================================================


class TestGetProject:
    def test_get_with_empty_playbooks(self, client):
        token = _get_token(client, "editor")
        p = client.post(
            "/api/projects",
            json={"name": _unique("get"), "namespace": _unique_ns()},
            headers=_auth(token),
        ).json()
        resp = client.get(f"/api/projects/{p['id']}", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == p["id"]
        assert data["namespace"] == p["namespace"]
        assert data["playbooks_enabled"] is True
        assert data["playbooks"] == []

    def test_get_with_playbooks(self, client):
        token = _get_token(client, "editor")
        p = client.post(
            "/api/projects",
            json={"name": _unique("with-pb"), "namespace": _unique_ns()},
            headers=_auth(token),
        ).json()

        pb_name = _unique("pb")
        pb_resp = client.post(
            "/api/playbooks",
            json={"name": pb_name, "project_id": p["id"]},
            headers=_auth(token),
        )
        assert pb_resp.status_code == 201

        resp = client.get(f"/api/projects/{p['id']}", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        pb_names = [pb["name"] for pb in data["playbooks"]]
        assert pb_name in pb_names

    def test_get_nonexistent_404(self, client):
        token = _get_token(client)
        resp = client.get(f"/api/projects/{uuid4()}", headers=_auth(token))
        assert resp.status_code == 404


# ===================================================================
# UPDATE
# ===================================================================


class TestUpdateProject:
    def test_update_name_as_editor(self, client):
        token = _get_token(client, "editor")
        p = client.post(
            "/api/projects",
            json={"name": _unique(), "namespace": _unique_ns()},
            headers=_auth(token),
        ).json()
        resp = client.patch(
            f"/api/projects/{p['id']}",
            json={"name": "renamed", "description": "new desc"},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "renamed"
        assert resp.json()["description"] == "new desc"

    def test_update_playbooks_enabled_requires_operator(self, client):
        """Flipping the kill switch is an operator+ action, same gate as the
        previous status field used. Editors can rename/describe but can't
        toggle remediation."""
        editor = _get_token(client, "editor")
        operator = _get_token(client, "operator")
        p = client.post(
            "/api/projects",
            json={"name": _unique(), "namespace": _unique_ns()},
            headers=_auth(editor),
        ).json()

        # Editor cannot disable playbooks
        resp = client.patch(
            f"/api/projects/{p['id']}",
            json={"playbooks_enabled": False},
            headers=_auth(editor),
        )
        assert resp.status_code == 403

        # Operator can
        resp = client.patch(
            f"/api/projects/{p['id']}",
            json={"playbooks_enabled": False},
            headers=_auth(operator),
        )
        assert resp.status_code == 200
        assert resp.json()["playbooks_enabled"] is False

    def test_update_name_viewer_forbidden(self, client):
        editor = _get_token(client, "editor")
        viewer = _get_token(client, "viewer")
        p = client.post(
            "/api/projects",
            json={"name": _unique(), "namespace": _unique_ns()},
            headers=_auth(editor),
        ).json()
        resp = client.patch(
            f"/api/projects/{p['id']}",
            json={"name": "nope"},
            headers=_auth(viewer),
        )
        assert resp.status_code == 403

    def test_update_empty_body_422(self, client):
        token = _get_token(client, "editor")
        p = client.post(
            "/api/projects",
            json={"name": _unique(), "namespace": _unique_ns()},
            headers=_auth(token),
        ).json()
        resp = client.patch(
            f"/api/projects/{p['id']}",
            json={},
            headers=_auth(token),
        )
        assert resp.status_code == 422

    def test_update_nonexistent_404(self, client):
        token = _get_token(client, "editor")
        resp = client.patch(
            f"/api/projects/{uuid4()}",
            json={"name": "x"},
            headers=_auth(token),
        )
        assert resp.status_code == 404


# ===================================================================
# DELETE
# ===================================================================


class TestDeleteProject:
    def test_delete_success(self, client):
        token = _get_token(client, "admin")
        p = client.post(
            "/api/projects",
            json={"name": _unique(), "namespace": _unique_ns()},
            headers=_auth(token),
        ).json()
        resp = client.delete(f"/api/projects/{p['id']}", headers=_auth(token))
        assert resp.status_code == 204
        assert client.get(f"/api/projects/{p['id']}", headers=_auth(token)).status_code == 404

    def test_delete_cascades_playbooks(self, client):
        token = _get_token(client, "admin")
        p = client.post(
            "/api/projects",
            json={"name": _unique(), "namespace": _unique_ns()},
            headers=_auth(token),
        ).json()
        pb = client.post(
            "/api/playbooks",
            json={"name": _unique("pb"), "project_id": p["id"]},
            headers=_auth(token),
        ).json()

        client.delete(f"/api/projects/{p['id']}", headers=_auth(token))

        resp = client.get(f"/api/playbooks/{pb['id']}", headers=_auth(token))
        assert resp.status_code == 404

    def test_delete_nonexistent_404(self, client):
        token = _get_token(client, "admin")
        resp = client.delete(f"/api/projects/{uuid4()}", headers=_auth(token))
        assert resp.status_code == 404

    def test_delete_non_admin_forbidden(self, client):
        editor = _get_token(client, "editor")
        resp = client.delete(f"/api/projects/{uuid4()}", headers=_auth(editor))
        assert resp.status_code == 403


# ===================================================================
# NAMESPACE-AFTER-DELETE REUSE
# ===================================================================


class TestNamespaceReuse:
    def test_namespace_reusable_after_project_deleted(self, client):
        """Deleting a project frees its namespace for a new project."""
        admin = _get_token(client, "admin")
        ns = _unique_ns("reuse")

        first = client.post(
            "/api/projects",
            json={"name": _unique("first"), "namespace": ns},
            headers=_auth(admin),
        ).json()
        client.delete(f"/api/projects/{first['id']}", headers=_auth(admin))

        second = client.post(
            "/api/projects",
            json={"name": _unique("second"), "namespace": ns},
            headers=_auth(admin),
        )
        assert second.status_code == 201
        assert second.json()["namespace"] == ns
