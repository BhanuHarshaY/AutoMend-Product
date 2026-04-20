"""Authentication routes (§25).

POST /api/auth/login     — Login (returns access + refresh JWT)
POST /api/auth/register  — Register new user (admin only)
GET  /api/auth/me        — Current user info
POST /api/auth/refresh   — Refresh access token
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.dependencies import get_current_user, get_db, require_role
from app.stores import postgres_store as store

router = APIRouter()

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str | None = None
    role: str = "viewer"


class UserResponse(BaseModel):
    id: UUID
    email: str
    display_name: str | None
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def _create_token(payload: dict[str, Any], expires_delta: timedelta) -> str:
    settings = get_settings()
    data = {
        **payload,
        "exp": datetime.now(timezone.utc) + expires_delta,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(data, settings.jwt_secret, algorithm="HS256")


def _create_access_token(user_id: str, email: str, role: str) -> str:
    settings = get_settings()
    return _create_token(
        {"sub": email, "user_id": user_id, "role": role, "type": "access"},
        timedelta(minutes=settings.jwt_expiry_minutes),
    )


def _create_refresh_token(user_id: str, email: str) -> str:
    settings = get_settings()
    return _create_token(
        {"sub": email, "user_id": user_id, "type": "refresh"},
        timedelta(days=settings.jwt_refresh_expiry_days),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_db)):
    """Authenticate with email + password, receive JWT tokens."""
    user = await store.get_user_by_email(session, body.email)
    if user is None or not user.hashed_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not pwd_context.verify(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")

    return TokenResponse(
        access_token=_create_access_token(str(user.id), user.email, user.role),
        refresh_token=_create_refresh_token(str(user.id), user.email),
    )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    body: RegisterRequest,
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_role("admin")),
):
    """Register a new user (admin only)."""
    existing = await store.get_user_by_email(session, body.email)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    valid_roles = {"admin", "operator", "editor", "viewer"}
    if body.role not in valid_roles:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid role. Must be one of: {', '.join(sorted(valid_roles))}",
        )
    user = await store.create_user(
        session,
        email=body.email,
        hashed_password=pwd_context.hash(body.password),
        display_name=body.display_name,
        role=body.role,
    )
    await session.commit()
    return user


@router.get("/me", response_model=UserResponse)
async def me(
    session: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """Return the currently authenticated user's info."""
    user = await store.get_user_by_email(session, current_user["sub"])
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(body: RefreshRequest, session: AsyncSession = Depends(get_db)):
    """Exchange a valid refresh token for a new access token."""
    settings = get_settings()
    try:
        payload = jwt.decode(body.refresh_token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not a refresh token")

    user = await store.get_user_by_email(session, payload["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or disabled")

    return AccessTokenResponse(
        access_token=_create_access_token(str(user.id), user.email, user.role),
    )
