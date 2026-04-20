"""Tests for auth routes: login, register, me, refresh.

Requires Postgres. Skips automatically if not available.
Uses a synchronous seed approach via psycopg2 to avoid event loop conflicts.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from passlib.context import CryptContext

try:
    import psycopg2
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.dependencies import get_db
    from app.models.db import Base
    from main_api import create_app

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False

TEST_DB_URL_ASYNC = "postgresql+asyncpg://automend:automend@localhost:5432/automend"
TEST_DB_URL_SYNC = "postgresql://automend:automend@localhost:5432/automend"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


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


def _unique_email() -> str:
    return f"test_{uuid4().hex[:8]}@example.com"


def _seed_user_sync(email: str, password: str, role: str = "viewer") -> None:
    """Insert a user directly via synchronous psycopg2 (no event loop issues)."""
    hashed = pwd_context.hash(password)
    conn = psycopg2.connect(
        dbname="automend", user="automend", password="automend",
        host="localhost", port=5432,
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (id, email, hashed_password, role, is_active, created_at) "
        "VALUES (gen_random_uuid(), %s, %s, %s, true, now()) ON CONFLICT (email) DO NOTHING",
        (email, hashed, role),
    )
    cur.close()
    conn.close()


def _seed_disabled_user_sync(email: str, password: str) -> None:
    hashed = pwd_context.hash(password)
    conn = psycopg2.connect(
        dbname="automend", user="automend", password="automend",
        host="localhost", port=5432,
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (id, email, hashed_password, role, is_active, created_at) "
        "VALUES (gen_random_uuid(), %s, %s, 'viewer', false, now()) ON CONFLICT (email) DO NOTHING",
        (email, hashed),
    )
    cur.close()
    conn.close()


@pytest.fixture()
def client():
    """Create a TestClient backed by real Postgres.

    Uses the app's own lifespan to manage the async engine — no override.
    The lifespan calls init_dependencies() which creates the engine.
    We patch settings to point at the test DB (same in this case).
    """
    app = create_app()
    # Use with statement so lifespan startup/shutdown run
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ===================================================================
# LOGIN
# ===================================================================


class TestLogin:
    def test_login_success(self, client):
        email = _unique_email()
        _seed_user_sync(email, "password123", "operator")
        resp = client.post("/api/auth/login", json={"email": email, "password": "password123"})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        # Verify claims
        settings = get_settings()
        payload = jwt.decode(data["access_token"], settings.jwt_secret, algorithms=["HS256"])
        assert payload["sub"] == email
        assert payload["role"] == "operator"
        assert payload["type"] == "access"

    def test_login_wrong_password(self, client):
        email = _unique_email()
        _seed_user_sync(email, "correct")
        resp = client.post("/api/auth/login", json={"email": email, "password": "wrong"})
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, client):
        resp = client.post("/api/auth/login", json={"email": "nope@x.com", "password": "x"})
        assert resp.status_code == 401

    def test_login_disabled_user(self, client):
        email = _unique_email()
        _seed_disabled_user_sync(email, "pw")
        resp = client.post("/api/auth/login", json={"email": email, "password": "pw"})
        assert resp.status_code == 403


# ===================================================================
# REGISTER
# ===================================================================


class TestRegister:
    def _admin_token(self, client, email=None, password="pw"):
        email = email or _unique_email()
        _seed_user_sync(email, password, "admin")
        resp = client.post("/api/auth/login", json={"email": email, "password": password})
        return resp.json()["access_token"]

    def test_register_success(self, client):
        token = self._admin_token(client)
        new_email = _unique_email()
        resp = client.post(
            "/api/auth/register",
            json={"email": new_email, "password": "newpw", "display_name": "Test", "role": "editor"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == new_email
        assert data["role"] == "editor"
        assert data["display_name"] == "Test"

    def test_register_duplicate_email(self, client):
        token = self._admin_token(client)
        dup_email = _unique_email()
        _seed_user_sync(dup_email, "pw")
        resp = client.post(
            "/api/auth/register",
            json={"email": dup_email, "password": "pw"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 409

    def test_register_non_admin_forbidden(self, client):
        viewer_email = _unique_email()
        _seed_user_sync(viewer_email, "pw", "viewer")
        resp = client.post("/api/auth/login", json={"email": viewer_email, "password": "pw"})
        token = resp.json()["access_token"]
        resp = client.post(
            "/api/auth/register",
            json={"email": _unique_email(), "password": "pw"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403

    def test_register_invalid_role(self, client):
        token = self._admin_token(client)
        resp = client.post(
            "/api/auth/register",
            json={"email": _unique_email(), "password": "pw", "role": "superadmin"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422


# ===================================================================
# ME
# ===================================================================


class TestMe:
    def test_me_returns_user_info(self, client):
        email = _unique_email()
        _seed_user_sync(email, "pw", "operator")
        resp = client.post("/api/auth/login", json={"email": email, "password": "pw"})
        token = resp.json()["access_token"]
        resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == email
        assert data["role"] == "operator"

    def test_me_no_token_returns_403(self, client):
        resp = client.get("/api/auth/me")
        assert resp.status_code == 403


# ===================================================================
# REFRESH
# ===================================================================


class TestRefresh:
    def test_refresh_returns_new_access_token(self, client):
        email = _unique_email()
        _seed_user_sync(email, "pw", "editor")
        resp = client.post("/api/auth/login", json={"email": email, "password": "pw"})
        refresh_token = resp.json()["refresh_token"]

        resp = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        settings = get_settings()
        payload = jwt.decode(data["access_token"], settings.jwt_secret, algorithms=["HS256"])
        assert payload["sub"] == email
        assert payload["role"] == "editor"

    def test_refresh_with_access_token_fails(self, client):
        email = _unique_email()
        _seed_user_sync(email, "pw")
        resp = client.post("/api/auth/login", json={"email": email, "password": "pw"})
        access_token = resp.json()["access_token"]
        resp = client.post("/api/auth/refresh", json={"refresh_token": access_token})
        assert resp.status_code == 401

    def test_refresh_with_invalid_token_fails(self, client):
        resp = client.post("/api/auth/refresh", json={"refresh_token": "garbage"})
        assert resp.status_code == 401

    def test_refresh_with_expired_token_fails(self, client):
        settings = get_settings()
        expired = jwt.encode(
            {"sub": "a@b.com", "type": "refresh", "exp": datetime.now(timezone.utc) - timedelta(hours=1)},
            settings.jwt_secret, algorithm="HS256",
        )
        resp = client.post("/api/auth/refresh", json={"refresh_token": expired})
        assert resp.status_code == 401
