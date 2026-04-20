"""Tests for app.dependencies — database, Redis, Temporal, and auth injection.

All tests run without live infrastructure by manipulating module-level state
or using mocks / in-memory objects.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from app.config import get_settings
from app import dependencies as deps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_token(payload: dict[str, Any], secret: str | None = None, exp_minutes: int = 60) -> str:
    """Create a signed JWT for testing."""
    settings = get_settings()
    data = {
        **payload,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=exp_minutes),
    }
    return jwt.encode(data, secret or settings.jwt_secret, algorithm="HS256")


def _make_expired_token(payload: dict[str, Any]) -> str:
    settings = get_settings()
    data = {
        **payload,
        "exp": datetime.now(timezone.utc) - timedelta(minutes=10),
    }
    return jwt.encode(data, settings.jwt_secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# Database dependency
# ---------------------------------------------------------------------------


class TestGetDb:
    def test_raises_when_not_initialized(self):
        original = deps._session_factory
        deps._session_factory = None
        try:
            gen = deps.get_db()
            with pytest.raises(RuntimeError, match="Database not initialized"):
                asyncio.get_event_loop().run_until_complete(gen.__anext__())
        finally:
            deps._session_factory = original

    def test_yields_session_when_initialized(self):
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock(return_value=mock_session)
        original = deps._session_factory
        deps._session_factory = mock_factory
        try:
            gen = deps.get_db()
            session = asyncio.get_event_loop().run_until_complete(gen.__anext__())
            assert session is mock_session
        finally:
            deps._session_factory = original


# ---------------------------------------------------------------------------
# Redis dependency
# ---------------------------------------------------------------------------


class TestGetRedis:
    def test_raises_when_not_initialized(self):
        original = deps._redis
        deps._redis = None
        try:
            with pytest.raises(RuntimeError, match="Redis not initialized"):
                asyncio.get_event_loop().run_until_complete(deps.get_redis())
        finally:
            deps._redis = original

    def test_returns_client_when_initialized(self):
        mock_redis = MagicMock()
        original = deps._redis
        deps._redis = mock_redis
        try:
            result = asyncio.get_event_loop().run_until_complete(deps.get_redis())
            assert result is mock_redis
        finally:
            deps._redis = original


# ---------------------------------------------------------------------------
# Temporal dependency
# ---------------------------------------------------------------------------


class TestGetTemporalClient:
    def test_raises_when_not_initialized(self):
        original = deps._temporal
        deps._temporal = None
        try:
            with pytest.raises(RuntimeError, match="Temporal client not available"):
                asyncio.get_event_loop().run_until_complete(deps.get_temporal_client())
        finally:
            deps._temporal = original

    def test_returns_client_when_initialized(self):
        mock_temporal = MagicMock()
        original = deps._temporal
        deps._temporal = mock_temporal
        try:
            result = asyncio.get_event_loop().run_until_complete(deps.get_temporal_client())
            assert result is mock_temporal
        finally:
            deps._temporal = original


# ---------------------------------------------------------------------------
# Init / cleanup lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    @patch("app.dependencies.TemporalClient")
    @patch("app.dependencies.Redis")
    @patch("app.dependencies.create_async_engine")
    def test_init_creates_engine_and_redis(self, mock_engine_fn, mock_redis_cls, mock_temporal_cls):
        mock_engine = MagicMock()
        mock_engine_fn.return_value = mock_engine
        mock_redis_instance = MagicMock()
        mock_redis_cls.from_url.return_value = mock_redis_instance
        # Temporal connect is async
        mock_temporal_cls.connect = AsyncMock(return_value=MagicMock())

        asyncio.get_event_loop().run_until_complete(deps.init_dependencies())

        mock_engine_fn.assert_called_once()
        mock_redis_cls.from_url.assert_called_once()
        assert deps._engine is mock_engine
        assert deps._redis is mock_redis_instance
        assert deps._session_factory is not None

    @patch("app.dependencies.TemporalClient")
    @patch("app.dependencies.Redis")
    @patch("app.dependencies.create_async_engine")
    def test_init_tolerates_temporal_failure(self, mock_engine_fn, mock_redis_cls, mock_temporal_cls):
        mock_engine_fn.return_value = MagicMock()
        mock_redis_cls.from_url.return_value = MagicMock()
        mock_temporal_cls.connect = AsyncMock(side_effect=Exception("connection refused"))

        asyncio.get_event_loop().run_until_complete(deps.init_dependencies())

        # Should not raise — temporal is optional at startup
        assert deps._temporal is None

    def test_cleanup_resets_state(self):
        deps._engine = MagicMock()
        deps._engine.dispose = AsyncMock()
        deps._redis = MagicMock()
        deps._redis.aclose = AsyncMock()
        deps._session_factory = MagicMock()
        deps._temporal = MagicMock()

        asyncio.get_event_loop().run_until_complete(deps.cleanup_dependencies())

        assert deps._engine is None
        assert deps._session_factory is None
        assert deps._redis is None
        assert deps._temporal is None

    def test_cleanup_handles_already_none(self):
        deps._engine = None
        deps._redis = None
        deps._session_factory = None
        deps._temporal = None

        # Should not raise
        asyncio.get_event_loop().run_until_complete(deps.cleanup_dependencies())


# ---------------------------------------------------------------------------
# Auth: get_current_user via a real FastAPI test app
# ---------------------------------------------------------------------------


def _build_auth_app() -> FastAPI:
    """Build a minimal FastAPI app with an auth-protected endpoint."""
    app = FastAPI()

    @app.get("/me")
    async def me(user: dict = Depends(deps.get_current_user)):
        return user

    @app.get("/admin")
    async def admin(user: dict = Depends(deps.require_role("admin"))):
        return user

    @app.get("/operator")
    async def operator(user: dict = Depends(deps.require_role("operator"))):
        return user

    return app


class TestGetCurrentUser:
    def setup_method(self):
        self.app = _build_auth_app()
        self.client = TestClient(self.app)

    def test_valid_token_returns_payload(self):
        token = _make_token({"sub": "user@example.com", "role": "admin"})
        resp = self.client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["sub"] == "user@example.com"
        assert data["role"] == "admin"

    def test_expired_token_returns_401(self):
        token = _make_expired_token({"sub": "user@example.com", "role": "admin"})
        resp = self.client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert "expired" in resp.json()["detail"].lower()

    def test_invalid_token_returns_401(self):
        resp = self.client.get("/me", headers={"Authorization": "Bearer not.a.jwt"})
        assert resp.status_code == 401
        assert "invalid" in resp.json()["detail"].lower()

    def test_wrong_secret_returns_401(self):
        token = _make_token({"sub": "user@example.com"}, secret="wrong-secret")
        resp = self.client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_missing_auth_header_returns_403(self):
        resp = self.client.get("/me")
        assert resp.status_code == 403  # HTTPBearer returns 403 if no header


# ---------------------------------------------------------------------------
# Auth: require_role
# ---------------------------------------------------------------------------


class TestRequireRole:
    def setup_method(self):
        self.app = _build_auth_app()
        self.client = TestClient(self.app)

    def test_admin_can_access_admin_route(self):
        token = _make_token({"sub": "a@b.com", "role": "admin"})
        resp = self.client.get("/admin", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_viewer_cannot_access_admin_route(self):
        token = _make_token({"sub": "a@b.com", "role": "viewer"})
        resp = self.client.get("/admin", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403
        assert "insufficient" in resp.json()["detail"].lower()

    def test_operator_can_access_operator_route(self):
        token = _make_token({"sub": "a@b.com", "role": "operator"})
        resp = self.client.get("/operator", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_admin_can_access_operator_route(self):
        token = _make_token({"sub": "a@b.com", "role": "admin"})
        resp = self.client.get("/operator", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200

    def test_editor_cannot_access_operator_route(self):
        token = _make_token({"sub": "a@b.com", "role": "editor"})
        resp = self.client.get("/operator", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_unknown_role_is_denied(self):
        token = _make_token({"sub": "a@b.com", "role": "hacker"})
        resp = self.client.get("/admin", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403

    def test_missing_role_field_is_denied(self):
        token = _make_token({"sub": "a@b.com"})
        resp = self.client.get("/admin", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Role hierarchy correctness
# ---------------------------------------------------------------------------


class TestRoleHierarchy:
    """Verify the exact hierarchy: admin > operator > editor > viewer."""

    ROLES_ASCENDING = ["viewer", "editor", "operator", "admin"]

    @pytest.mark.parametrize("role_idx", range(4))
    def test_role_can_access_own_level(self, role_idx):
        """Each role can access routes requiring its own level."""
        role = self.ROLES_ASCENDING[role_idx]
        app = FastAPI()

        @app.get("/test")
        async def test_endpoint(user: dict = Depends(deps.require_role(role))):
            return user

        client = TestClient(app)
        token = _make_token({"sub": "u@b.com", "role": role})
        resp = client.get("/test", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Module-level exports
# ---------------------------------------------------------------------------


class TestExports:
    """Verify all expected symbols are exported from the module."""

    EXPECTED = [
        "init_dependencies",
        "cleanup_dependencies",
        "get_db",
        "get_redis",
        "get_temporal_client",
        "get_current_user",
        "require_role",
        "security",
    ]

    @pytest.mark.parametrize("name", EXPECTED)
    def test_export_exists(self, name):
        assert hasattr(deps, name)
