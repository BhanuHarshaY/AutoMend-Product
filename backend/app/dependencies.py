"""FastAPI dependency injection.

Provides database sessions, Redis client, Temporal client, and auth
dependencies for route handlers. Module-level state is initialized at
app startup via ``init_dependencies()`` and torn down at shutdown via
``cleanup_dependencies()``, both called from the lifespan in main_api.py.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from temporalio.client import Client as TemporalClient

from app.config import get_settings

# ---------------------------------------------------------------------------
# Module-level state — populated by init_dependencies, cleared by cleanup
# ---------------------------------------------------------------------------

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_redis: Redis | None = None
_temporal: TemporalClient | None = None


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def init_dependencies() -> None:
    """Create connection pools and clients. Called once at app startup."""
    global _engine, _session_factory, _redis, _temporal

    settings = get_settings()

    # --- Postgres (async) ---
    _engine = create_async_engine(
        settings.postgres_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )
    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # --- Redis ---
    _redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
    )

    # --- Temporal ---
    try:
        _temporal = await TemporalClient.connect(
            settings.temporal_server_url,
            namespace=settings.temporal_namespace,
            lazy=True,  # Don't connect until first RPC call — avoids blocking startup
        )
    except Exception:
        # Temporal may not be running during early dev / tests.
        # Routes that need it will fail with a clear error via get_temporal_client.
        _temporal = None


async def cleanup_dependencies() -> None:
    """Close connections. Called once at app shutdown."""
    global _engine, _session_factory, _redis, _temporal

    if _redis is not None:
        await _redis.aclose()
        _redis = None

    if _engine is not None:
        await _engine.dispose()
        _engine = None

    _session_factory = None
    _temporal = None


# ---------------------------------------------------------------------------
# FastAPI Dependencies — database
# ---------------------------------------------------------------------------


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session, auto-closing on exit."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized — call init_dependencies first")
    async with _session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# FastAPI Dependencies — Redis
# ---------------------------------------------------------------------------


async def get_redis() -> Redis:
    """Return the shared Redis client."""
    if _redis is None:
        raise RuntimeError("Redis not initialized — call init_dependencies first")
    return _redis


# ---------------------------------------------------------------------------
# FastAPI Dependencies — Temporal
# ---------------------------------------------------------------------------


async def get_temporal_client() -> TemporalClient:
    """Return the shared Temporal client."""
    if _temporal is None:
        raise RuntimeError(
            "Temporal client not available — is Temporal server running?"
        )
    return _temporal


# ---------------------------------------------------------------------------
# FastAPI Dependencies — Authentication (§25.3)
# ---------------------------------------------------------------------------

security = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict[str, Any]:
    """Decode and validate a JWT bearer token, returning the payload."""
    settings = get_settings()
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


def require_role(required_role: str):
    """Return a dependency that enforces a minimum role level."""

    async def check_role(
        user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        role_hierarchy = {"admin": 4, "operator": 3, "editor": 2, "viewer": 1}
        user_level = role_hierarchy.get(user.get("role", ""), 0)
        required_level = role_hierarchy.get(required_role, 0)
        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return check_role
