"""Tests for workflow status API routes.

Uses a mocked Temporal client — no Temporal server needed.
Requires Postgres for auth (login to get JWT).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock
from uuid import uuid4

import pytest

try:
    import psycopg2
    from passlib.context import CryptContext
    from fastapi.testclient import TestClient
    from temporalio.service import RPCError, RPCStatusCode

    from app.dependencies import get_temporal_client
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


def _unique(prefix: str = "w") -> str:
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


# ---------------------------------------------------------------------------
# Mock Temporal helpers
# ---------------------------------------------------------------------------


def _mock_workflow_execution(wf_id: str = "wf-1", run_id: str = "run-1"):
    """Create a mock workflow execution object as returned by list_workflows."""
    wf = MagicMock()
    wf.id = wf_id
    wf.run_id = run_id
    wf.workflow_type = "DynamicPlaybookExecutor"
    wf.status = MagicMock(name="RUNNING")
    wf.status.name = "RUNNING"
    wf.start_time = datetime(2025, 1, 15, 10, 30, tzinfo=timezone.utc)
    wf.close_time = None
    return wf


def _mock_workflow_description(wf_id: str = "wf-1", run_id: str = "run-1"):
    """Create a mock describe() result."""
    desc = MagicMock()
    desc.id = wf_id
    desc.run_id = run_id
    desc.workflow_type = "DynamicPlaybookExecutor"
    desc.status = MagicMock(name="RUNNING")
    desc.status.name = "RUNNING"
    desc.task_queue = "automend-playbook-queue"
    desc.start_time = datetime(2025, 1, 15, 10, 30, tzinfo=timezone.utc)
    desc.close_time = None
    desc.execution_time = None
    desc.history_length = 42
    desc.memo = {}
    desc.search_attributes = {}
    return desc


def _make_mock_temporal(
    workflows=None,
    describe_result=None,
    describe_error=None,
    signal_error=None,
    cancel_error=None,
):
    """Build a mock TemporalClient with configurable behavior."""
    mock = MagicMock()

    # list_workflows returns an async iterator
    async def _list_workflows(**kwargs):
        for wf in (workflows or []):
            yield wf

    mock.list_workflows = _list_workflows

    # get_workflow_handle returns a handle mock
    handle = MagicMock()
    if describe_error:
        handle.describe = AsyncMock(side_effect=describe_error)
    else:
        handle.describe = AsyncMock(return_value=describe_result or _mock_workflow_description())

    if signal_error:
        handle.signal = AsyncMock(side_effect=signal_error)
    else:
        handle.signal = AsyncMock()

    if cancel_error:
        handle.cancel = AsyncMock(side_effect=cancel_error)
    else:
        handle.cancel = AsyncMock()

    mock.get_workflow_handle = MagicMock(return_value=handle)

    return mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def make_client():
    """Factory that returns a TestClient with a custom mock Temporal injected."""

    def _build(mock_temporal=None):
        app = create_app()
        if mock_temporal is not None:
            app.dependency_overrides[get_temporal_client] = lambda: mock_temporal
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
        app.dependency_overrides.clear()

    return _build


@pytest.fixture()
def client(make_client):
    """Default client with a basic mock Temporal."""
    mock = _make_mock_temporal(
        workflows=[_mock_workflow_execution("wf-1"), _mock_workflow_execution("wf-2")],
    )
    yield from make_client(mock)


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


class TestListWorkflows:
    def test_list_returns_workflows(self, client):
        token = _get_token(client)
        resp = client.get("/api/workflows", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["workflow_id"] == "wf-1"
        assert data[0]["status"] == "RUNNING"
        assert data[0]["start_time"] is not None

    def test_list_empty(self, make_client):
        mock = _make_mock_temporal(workflows=[])
        for c in make_client(mock):
            token = _get_token(c)
            resp = c.get("/api/workflows", headers=_auth(token))
            assert resp.status_code == 200
            assert resp.json() == []

    def test_list_requires_auth(self, client):
        resp = client.get("/api/workflows")
        assert resp.status_code == 403


# ===================================================================
# GET DETAIL
# ===================================================================


class TestGetWorkflow:
    def test_get_detail(self, client):
        token = _get_token(client)
        resp = client.get("/api/workflows/wf-1", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["workflow_id"] == "wf-1"
        assert data["task_queue"] == "automend-playbook-queue"
        assert data["history_length"] == 42

    def test_get_not_found(self, make_client):
        mock = _make_mock_temporal(
            describe_error=RPCError("not found", RPCStatusCode.NOT_FOUND, b""),
        )
        for c in make_client(mock):
            token = _get_token(c)
            resp = c.get("/api/workflows/nonexistent", headers=_auth(token))
            assert resp.status_code == 404


# ===================================================================
# SIGNAL
# ===================================================================


class TestSignalWorkflow:
    def test_signal_success(self, client):
        token = _get_token(client)
        resp = client.post(
            "/api/workflows/wf-1/signal",
            json={"signal_name": "new_evidence", "payload": {"key": "value"}},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert "signal" in resp.json()["message"].lower()

    def test_signal_not_found(self, make_client):
        mock = _make_mock_temporal(
            signal_error=RPCError("not found", RPCStatusCode.NOT_FOUND, b""),
        )
        for c in make_client(mock):
            token = _get_token(c)
            resp = c.post(
                "/api/workflows/wf-gone/signal",
                json={"signal_name": "test", "payload": {}},
                headers=_auth(token),
            )
            assert resp.status_code == 404

    def test_signal_viewer_forbidden(self, client):
        token = _get_token(client, "viewer")
        resp = client.post(
            "/api/workflows/wf-1/signal",
            json={"signal_name": "test"},
            headers=_auth(token),
        )
        assert resp.status_code == 403


# ===================================================================
# CANCEL
# ===================================================================


class TestCancelWorkflow:
    def test_cancel_success(self, client):
        token = _get_token(client)
        resp = client.post("/api/workflows/wf-1/cancel", headers=_auth(token))
        assert resp.status_code == 200
        assert "cancel" in resp.json()["message"].lower()

    def test_cancel_not_found(self, make_client):
        mock = _make_mock_temporal(
            cancel_error=RPCError("not found", RPCStatusCode.NOT_FOUND, b""),
        )
        for c in make_client(mock):
            token = _get_token(c)
            resp = c.post("/api/workflows/wf-gone/cancel", headers=_auth(token))
            assert resp.status_code == 404

    def test_cancel_viewer_forbidden(self, client):
        token = _get_token(client, "viewer")
        resp = client.post("/api/workflows/wf-1/cancel", headers=_auth(token))
        assert resp.status_code == 403
