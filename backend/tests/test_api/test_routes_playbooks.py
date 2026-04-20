"""Tests for playbooks API routes.

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


def _unique(prefix: str = "p") -> str:
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


SAMPLE_SPEC = {"name": "test", "version": "1.0.0", "trigger": {"incident_types": ["incident.test"]}, "steps": []}


# ===================================================================
# CREATE
# ===================================================================


class TestCreatePlaybook:
    def test_create_success(self, client):
        token = _get_token(client, "editor")
        resp = client.post(
            "/api/playbooks",
            json={"name": _unique("pb"), "description": "A test playbook", "owner_team": "platform"},
            headers=_auth(token),
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert data["description"] == "A test playbook"
        assert data["owner_team"] == "platform"

    def test_create_viewer_forbidden(self, client):
        token = _get_token(client, "viewer")
        resp = client.post(
            "/api/playbooks",
            json={"name": _unique("pb")},
            headers=_auth(token),
        )
        assert resp.status_code == 403


# ===================================================================
# LIST
# ===================================================================


class TestListPlaybooks:
    def test_list(self, client):
        token = _get_token(client, "editor")
        name = _unique("list")
        client.post("/api/playbooks", json={"name": name}, headers=_auth(token))
        resp = client.get("/api/playbooks", headers=_auth(token))
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()]
        assert name in names


# ===================================================================
# GET (with versions)
# ===================================================================


class TestGetPlaybook:
    def test_get_with_versions(self, client):
        token = _get_token(client, "editor")
        create_resp = client.post("/api/playbooks", json={"name": _unique("get")}, headers=_auth(token))
        pb_id = create_resp.json()["id"]

        # Add a version
        client.post(
            f"/api/playbooks/{pb_id}/versions",
            json={"workflow_spec": SAMPLE_SPEC},
            headers=_auth(token),
        )

        resp = client.get(f"/api/playbooks/{pb_id}", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == pb_id
        assert len(data["versions"]) == 1
        assert data["versions"][0]["version_number"] == 1

    def test_get_nonexistent_404(self, client):
        token = _get_token(client)
        resp = client.get(f"/api/playbooks/{uuid4()}", headers=_auth(token))
        assert resp.status_code == 404


# ===================================================================
# GET VERSION
# ===================================================================


class TestGetVersion:
    def test_get_specific_version(self, client):
        token = _get_token(client, "editor")
        pb_id = client.post("/api/playbooks", json={"name": _unique()}, headers=_auth(token)).json()["id"]
        v_resp = client.post(
            f"/api/playbooks/{pb_id}/versions",
            json={"workflow_spec": SAMPLE_SPEC, "change_notes": "v1"},
            headers=_auth(token),
        )
        v_id = v_resp.json()["id"]

        resp = client.get(f"/api/playbooks/{pb_id}/versions/{v_id}", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json()["workflow_spec"] == SAMPLE_SPEC
        assert resp.json()["change_notes"] == "v1"

    def test_get_version_wrong_playbook_404(self, client):
        token = _get_token(client, "editor")
        pb_id = client.post("/api/playbooks", json={"name": _unique()}, headers=_auth(token)).json()["id"]
        v_id = client.post(
            f"/api/playbooks/{pb_id}/versions",
            json={"workflow_spec": SAMPLE_SPEC},
            headers=_auth(token),
        ).json()["id"]
        # Use a different playbook ID
        resp = client.get(f"/api/playbooks/{uuid4()}/versions/{v_id}", headers=_auth(token))
        assert resp.status_code == 404


# ===================================================================
# SAVE VERSION
# ===================================================================


class TestSaveVersion:
    def test_auto_increments(self, client):
        token = _get_token(client, "editor")
        pb_id = client.post("/api/playbooks", json={"name": _unique()}, headers=_auth(token)).json()["id"]

        v1 = client.post(f"/api/playbooks/{pb_id}/versions", json={"workflow_spec": {"v": 1}}, headers=_auth(token))
        v2 = client.post(f"/api/playbooks/{pb_id}/versions", json={"workflow_spec": {"v": 2}}, headers=_auth(token))
        assert v1.json()["version_number"] == 1
        assert v2.json()["version_number"] == 2

    def test_save_to_nonexistent_playbook_404(self, client):
        token = _get_token(client, "editor")
        resp = client.post(
            f"/api/playbooks/{uuid4()}/versions",
            json={"workflow_spec": SAMPLE_SPEC},
            headers=_auth(token),
        )
        assert resp.status_code == 404

    def test_save_has_checksum(self, client):
        token = _get_token(client, "editor")
        pb_id = client.post("/api/playbooks", json={"name": _unique()}, headers=_auth(token)).json()["id"]
        resp = client.post(
            f"/api/playbooks/{pb_id}/versions",
            json={"workflow_spec": SAMPLE_SPEC},
            headers=_auth(token),
        )
        assert resp.status_code == 201
        assert len(resp.json()["spec_checksum"]) == 64  # SHA-256 hex


# ===================================================================
# STATUS TRANSITIONS
# ===================================================================


class TestStatusTransition:
    def _create_draft(self, client, token):
        pb_id = client.post("/api/playbooks", json={"name": _unique()}, headers=_auth(token)).json()["id"]
        v = client.post(
            f"/api/playbooks/{pb_id}/versions",
            json={"workflow_spec": SAMPLE_SPEC},
            headers=_auth(token),
        ).json()
        return pb_id, v["id"]

    def test_draft_to_validated(self, client):
        token = _get_token(client, "operator")
        pb_id, v_id = self._create_draft(client, token)
        resp = client.patch(
            f"/api/playbooks/{pb_id}/versions/{v_id}/status",
            json={"new_status": "validated"},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.json()["new_status"] == "validated"

    def test_full_lifecycle(self, client):
        token = _get_token(client, "admin")
        pb_id, v_id = self._create_draft(client, token)
        path = f"/api/playbooks/{pb_id}/versions/{v_id}/status"

        for next_status in ["validated", "approved", "published", "archived"]:
            resp = client.patch(path, json={"new_status": next_status}, headers=_auth(token))
            assert resp.status_code == 200, f"Failed transitioning to {next_status}: {resp.json()}"
            assert resp.json()["new_status"] == next_status

    def test_invalid_transition_rejected(self, client):
        token = _get_token(client, "operator")
        pb_id, v_id = self._create_draft(client, token)
        # draft → published is not allowed (must go through validated → approved first)
        resp = client.patch(
            f"/api/playbooks/{pb_id}/versions/{v_id}/status",
            json={"new_status": "published"},
            headers=_auth(token),
        )
        assert resp.status_code == 422
        assert "cannot transition" in resp.json()["detail"].lower()

    def test_archived_is_terminal(self, client):
        token = _get_token(client, "admin")
        pb_id, v_id = self._create_draft(client, token)
        path = f"/api/playbooks/{pb_id}/versions/{v_id}/status"
        for s in ["validated", "approved", "published", "archived"]:
            client.patch(path, json={"new_status": s}, headers=_auth(token))
        # Can't transition from archived
        resp = client.patch(path, json={"new_status": "draft"}, headers=_auth(token))
        assert resp.status_code == 422

    def test_transition_viewer_forbidden(self, client):
        editor_token = _get_token(client, "editor")
        viewer_token = _get_token(client, "viewer")
        pb_id, v_id = self._create_draft(client, editor_token)
        resp = client.patch(
            f"/api/playbooks/{pb_id}/versions/{v_id}/status",
            json={"new_status": "validated"},
            headers=_auth(viewer_token),
        )
        assert resp.status_code == 403


# ===================================================================
# TASK 11.8e — AUTO-REPOINT TRIGGER RULES ON PUBLISH
# ===================================================================


def _insert_trigger_rule(incident_type: str, playbook_version_id: str) -> str:
    """Direct SQL INSERT — no API route exists for trigger rule creation yet
    (PROGRESS.md open follow-up). Returns the new rule's id."""
    conn = psycopg2.connect(
        dbname="automend", user="automend", password="automend",
        host="localhost", port=5432,
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO trigger_rules "
        "(id, incident_type, playbook_version_id, priority, is_active, created_at, updated_at) "
        "VALUES (gen_random_uuid(), %s, %s, 10, true, now(), now()) RETURNING id",
        (incident_type, playbook_version_id),
    )
    rid = cur.fetchone()[0]
    cur.close()
    conn.close()
    return str(rid)


def _get_trigger_rule_target(rule_id: str) -> str:
    conn = psycopg2.connect(
        dbname="automend", user="automend", password="automend",
        host="localhost", port=5432,
    )
    cur = conn.cursor()
    cur.execute("SELECT playbook_version_id FROM trigger_rules WHERE id = %s", (rule_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return str(row[0]) if row else ""


class TestPublishRepointsActiveRules:
    """When a new playbook version transitions to 'published', every active
    trigger rule that currently points at a prior version of the SAME playbook
    is auto-repointed to the new version. Retires the manual UPDATE walk-around
    previously needed after every publish."""

    def _publish(self, client, token, pb_id, v_id):
        """Walk a version from draft → published through the full chain."""
        path = f"/api/playbooks/{pb_id}/versions/{v_id}/status"
        for next_status in ["validated", "approved", "published"]:
            resp = client.patch(path, json={"new_status": next_status}, headers=_auth(token))
            assert resp.status_code == 200, f"publish step failed at {next_status}: {resp.json()}"

    def test_publish_v2_repoints_rule_from_v1(self, client):
        token = _get_token(client, "admin")
        # Playbook + version 1, published.
        pb_id = client.post("/api/playbooks", json={"name": _unique("pb")}, headers=_auth(token)).json()["id"]
        v1 = client.post(
            f"/api/playbooks/{pb_id}/versions",
            json={"workflow_spec": SAMPLE_SPEC},
            headers=_auth(token),
        ).json()
        self._publish(client, token, pb_id, v1["id"])

        # Register a trigger rule against v1.
        rule_id = _insert_trigger_rule(f"incident.test_{uuid4().hex[:8]}", v1["id"])
        assert _get_trigger_rule_target(rule_id) == v1["id"]

        # Save v2 (draft) on the same playbook, then publish it.
        v2 = client.post(
            f"/api/playbooks/{pb_id}/versions",
            json={"workflow_spec": {**SAMPLE_SPEC, "change": "v2"}},
            headers=_auth(token),
        ).json()
        assert v2["id"] != v1["id"]
        self._publish(client, token, pb_id, v2["id"])

        # Rule should now point at v2 automatically.
        assert _get_trigger_rule_target(rule_id) == v2["id"], \
            "Publishing v2 should have repointed the active trigger rule from v1 → v2"

    def test_repoint_does_not_touch_other_playbooks_rules(self, client):
        """Rules for OTHER playbooks are left alone — repointing is scoped to
        the publishing playbook's own version family."""
        token = _get_token(client, "admin")
        # Playbook A with v1 published + a rule pointing at v1.
        pb_a = client.post("/api/playbooks", json={"name": _unique("pb-a")}, headers=_auth(token)).json()["id"]
        v_a1 = client.post(
            f"/api/playbooks/{pb_a}/versions",
            json={"workflow_spec": SAMPLE_SPEC},
            headers=_auth(token),
        ).json()
        self._publish(client, token, pb_a, v_a1["id"])
        rule_a = _insert_trigger_rule(f"incident.a_{uuid4().hex[:8]}", v_a1["id"])

        # Playbook B with v1 → publish, then v2 → publish.
        pb_b = client.post("/api/playbooks", json={"name": _unique("pb-b")}, headers=_auth(token)).json()["id"]
        v_b1 = client.post(
            f"/api/playbooks/{pb_b}/versions",
            json={"workflow_spec": SAMPLE_SPEC},
            headers=_auth(token),
        ).json()
        self._publish(client, token, pb_b, v_b1["id"])
        v_b2 = client.post(
            f"/api/playbooks/{pb_b}/versions",
            json={"workflow_spec": {**SAMPLE_SPEC, "v": 2}},
            headers=_auth(token),
        ).json()
        self._publish(client, token, pb_b, v_b2["id"])

        # Rule for playbook A must still point at v_a1 — publishing on B
        # can't leak into A's rules.
        assert _get_trigger_rule_target(rule_a) == v_a1["id"], \
            "Publishing a version on playbook B should not touch playbook A's rules"

    def test_inactive_rule_not_repointed(self, client):
        """`is_active = false` rules are skipped — deactivated rules stay
        pinned to whatever version they originally targeted (historical accuracy)."""
        token = _get_token(client, "admin")
        pb_id = client.post("/api/playbooks", json={"name": _unique("pb")}, headers=_auth(token)).json()["id"]
        v1 = client.post(
            f"/api/playbooks/{pb_id}/versions",
            json={"workflow_spec": SAMPLE_SPEC},
            headers=_auth(token),
        ).json()
        self._publish(client, token, pb_id, v1["id"])

        rule_id = _insert_trigger_rule(f"incident.inactive_{uuid4().hex[:8]}", v1["id"])
        # Deactivate it.
        conn = psycopg2.connect(
            dbname="automend", user="automend", password="automend",
            host="localhost", port=5432,
        )
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("UPDATE trigger_rules SET is_active = false WHERE id = %s", (rule_id,))
        cur.close()
        conn.close()

        # Publish v2.
        v2 = client.post(
            f"/api/playbooks/{pb_id}/versions",
            json={"workflow_spec": {**SAMPLE_SPEC, "v": 2}},
            headers=_auth(token),
        ).json()
        self._publish(client, token, pb_id, v2["id"])

        # Inactive rule should still point at v1.
        assert _get_trigger_rule_target(rule_id) == v1["id"]


# ===================================================================
# DELETE
# ===================================================================


class TestDeletePlaybook:
    def test_delete_success(self, client):
        token = _get_token(client, "admin")
        pb_id = client.post("/api/playbooks", json={"name": _unique()}, headers=_auth(token)).json()["id"]
        resp = client.delete(f"/api/playbooks/{pb_id}", headers=_auth(token))
        assert resp.status_code == 204
        # Gone
        assert client.get(f"/api/playbooks/{pb_id}", headers=_auth(token)).status_code == 404

    def test_delete_nonexistent_404(self, client):
        token = _get_token(client, "admin")
        resp = client.delete(f"/api/playbooks/{uuid4()}", headers=_auth(token))
        assert resp.status_code == 404

    def test_delete_non_admin_forbidden(self, client):
        token = _get_token(client, "editor")
        resp = client.delete(f"/api/playbooks/{uuid4()}", headers=_auth(token))
        assert resp.status_code == 403
