"""Tests for incidents API routes.

Requires Postgres. Skips if not available.
"""

from __future__ import annotations

import json
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


def _unique(prefix: str = "i") -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def _seed_user(email: str, password: str, role: str = "operator") -> None:
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


def _seed_incident(incident_key: str | None = None, severity: str = "high", status: str = "open") -> str:
    """Insert an incident via psycopg2. Returns the incident ID."""
    ikey = incident_key or f"prod/ml/{uuid4().hex[:8]}"
    inc_id = str(uuid4())
    entity = json.dumps({"cluster": "prod-a", "namespace": "ml", "service": "trainer"})
    evidence = json.dumps({"metric_alerts": [], "raw_signals": []})
    conn = psycopg2.connect(
        dbname="automend", user="automend", password="automend",
        host="localhost", port=5432,
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO incidents (id, incident_key, incident_type, status, severity, "
        "entity, sources, evidence, created_at, updated_at) "
        "VALUES (%s, %s, 'incident.gpu_memory_failure', %s, %s, %s, "
        "'{log_classifier}', %s, now(), now())",
        (inc_id, ikey, status, severity, entity, evidence),
    )
    cur.close()
    conn.close()
    return inc_id


def _seed_event(incident_id: str, event_type: str = "created") -> None:
    conn = psycopg2.connect(
        dbname="automend", user="automend", password="automend",
        host="localhost", port=5432,
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO incident_events (id, incident_id, event_type, payload, actor, created_at) "
        "VALUES (gen_random_uuid(), %s, %s, '{}'::jsonb, 'system', now())",
        (incident_id, event_type),
    )
    cur.close()
    conn.close()


@pytest.fixture()
def client():
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _get_token(client, role="operator") -> str:
    email = _unique(role) + "@test.com"
    _seed_user(email, "pw", role)
    resp = client.post("/api/auth/login", json={"email": email, "password": "pw"})
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ===================================================================
# LIST
# ===================================================================


class TestListIncidents:
    def test_list_all(self, client):
        token = _get_token(client)
        _seed_incident()
        resp = client.get("/api/incidents", headers=_auth(token))
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_list_filter_by_severity(self, client):
        token = _get_token(client)
        _seed_incident(severity="critical")
        resp = client.get("/api/incidents?severity=critical", headers=_auth(token))
        assert resp.status_code == 200
        assert all(i["severity"] == "critical" for i in resp.json())

    def test_list_filter_by_status(self, client):
        token = _get_token(client)
        key = _unique("st")
        _seed_incident(incident_key=key, status="acknowledged")
        resp = client.get("/api/incidents?status=acknowledged", headers=_auth(token))
        assert resp.status_code == 200
        assert all(i["status"] == "acknowledged" for i in resp.json())

    def test_list_pagination(self, client):
        token = _get_token(client)
        resp = client.get("/api/incidents?limit=2&offset=0", headers=_auth(token))
        assert resp.status_code == 200
        assert len(resp.json()) <= 2


# ===================================================================
# GET DETAIL
# ===================================================================


class TestGetIncident:
    def test_get_by_id(self, client):
        token = _get_token(client)
        inc_id = _seed_incident()
        resp = client.get(f"/api/incidents/{inc_id}", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json()["id"] == inc_id

    def test_get_nonexistent_404(self, client):
        token = _get_token(client)
        resp = client.get(f"/api/incidents/{uuid4()}", headers=_auth(token))
        assert resp.status_code == 404


# ===================================================================
# UPDATE
# ===================================================================


class TestUpdateIncident:
    def test_update_status(self, client):
        token = _get_token(client)
        inc_id = _seed_incident()
        resp = client.patch(
            f"/api/incidents/{inc_id}",
            json={"status": "in_progress"},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

    def test_update_severity(self, client):
        token = _get_token(client)
        inc_id = _seed_incident(severity="medium")
        resp = client.patch(
            f"/api/incidents/{inc_id}",
            json={"severity": "critical"},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.json()["severity"] == "critical"

    def test_update_nonexistent_404(self, client):
        token = _get_token(client)
        resp = client.patch(
            f"/api/incidents/{uuid4()}",
            json={"status": "closed"},
            headers=_auth(token),
        )
        assert resp.status_code == 404

    def test_update_creates_event(self, client):
        token = _get_token(client)
        inc_id = _seed_incident()
        client.patch(
            f"/api/incidents/{inc_id}",
            json={"status": "acknowledged"},
            headers=_auth(token),
        )
        events_resp = client.get(f"/api/incidents/{inc_id}/events", headers=_auth(token))
        events = events_resp.json()
        assert any(e["event_type"] == "status_changed" for e in events)

    def test_update_viewer_forbidden(self, client):
        token = _get_token(client, "viewer")
        inc_id = _seed_incident()
        resp = client.patch(
            f"/api/incidents/{inc_id}",
            json={"status": "closed"},
            headers=_auth(token),
        )
        assert resp.status_code == 403


# ===================================================================
# ACKNOWLEDGE
# ===================================================================


class TestAcknowledge:
    def test_acknowledge(self, client):
        token = _get_token(client)
        inc_id = _seed_incident()
        resp = client.post(f"/api/incidents/{inc_id}/acknowledge", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json()["status"] == "acknowledged"

    def test_acknowledge_nonexistent_404(self, client):
        token = _get_token(client)
        resp = client.post(f"/api/incidents/{uuid4()}/acknowledge", headers=_auth(token))
        assert resp.status_code == 404


# ===================================================================
# RESOLVE
# ===================================================================


class TestResolve:
    def test_resolve(self, client):
        token = _get_token(client)
        inc_id = _seed_incident()
        resp = client.post(f"/api/incidents/{inc_id}/resolve", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "resolved"
        assert data["resolved_at"] is not None

    def test_resolve_nonexistent_404(self, client):
        token = _get_token(client)
        resp = client.post(f"/api/incidents/{uuid4()}/resolve", headers=_auth(token))
        assert resp.status_code == 404


# ===================================================================
# EVENTS
# ===================================================================


class TestEvents:
    def test_get_events_timeline(self, client):
        token = _get_token(client)
        inc_id = _seed_incident()
        _seed_event(inc_id, "created")
        _seed_event(inc_id, "signal_added")
        resp = client.get(f"/api/incidents/{inc_id}/events", headers=_auth(token))
        assert resp.status_code == 200
        events = resp.json()
        assert len(events) >= 2
        types = [e["event_type"] for e in events]
        assert "created" in types
        assert "signal_added" in types

    def test_events_nonexistent_incident_404(self, client):
        token = _get_token(client)
        resp = client.get(f"/api/incidents/{uuid4()}/events", headers=_auth(token))
        assert resp.status_code == 404


# ===================================================================
# WORKFLOW STATUS
# ===================================================================


class TestWorkflowStatus:
    def test_workflow_status_no_workflow(self, client):
        token = _get_token(client)
        inc_id = _seed_incident()
        resp = client.get(f"/api/incidents/{inc_id}/workflow", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["temporal_workflow_id"] is None
        assert data["status"] == "open"

    def test_workflow_nonexistent_404(self, client):
        token = _get_token(client)
        resp = client.get(f"/api/incidents/{uuid4()}/workflow", headers=_auth(token))
        assert resp.status_code == 404


# ===================================================================
# STATS
# ===================================================================


class TestStats:
    def test_get_stats(self, client):
        token = _get_token(client)
        _seed_incident(severity="high")
        resp = client.get("/api/incidents/stats", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert "by_status" in data
        assert "by_severity" in data
