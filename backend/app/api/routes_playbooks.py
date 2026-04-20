"""Playbook registry routes (§18, §23.2).

GET    /api/playbooks                                    — List all playbooks
POST   /api/playbooks                                    — Create a playbook
GET    /api/playbooks/{id}                               — Get playbook with versions
GET    /api/playbooks/{id}/versions/{version_id}         — Get specific version
POST   /api/playbooks/{id}/versions                      — Save new version (draft)
PATCH  /api/playbooks/{id}/versions/{version_id}/status  — Transition version status
DELETE /api/playbooks/{id}                               — Soft-delete playbook
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db, require_role
from app.domain.playbooks import (
    PlaybookCreate,
    PlaybookRead,
    PlaybookVersionCreate,
    PlaybookVersionRead,
    PlaybookVersionStatus,
    VALID_STATUS_TRANSITIONS,
)
from app.stores import postgres_store as store

router = APIRouter()


# ---------------------------------------------------------------------------
# Extra response schemas
# ---------------------------------------------------------------------------


class PlaybookDetailRead(PlaybookRead):
    """Playbook with its versions."""
    versions: list[PlaybookVersionRead] = []


class StatusTransitionRequest(BaseModel):
    new_status: PlaybookVersionStatus


class StatusTransitionResponse(BaseModel):
    version_id: UUID
    new_status: PlaybookVersionStatus


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[PlaybookRead])
async def list_playbooks(
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """List all playbooks."""
    return await store.list_playbooks(session)


@router.post("", response_model=PlaybookRead, status_code=status.HTTP_201_CREATED)
async def create_playbook(
    body: PlaybookCreate,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("editor")),
):
    """Create a new playbook (editor+)."""
    pb = await store.create_playbook(
        session,
        name=body.name,
        description=body.description,
        owner_team=body.owner_team,
        project_id=body.project_id,
        created_by=user.get("sub"),
    )
    await session.commit()
    return pb


@router.get("/{playbook_id}", response_model=PlaybookDetailRead)
async def get_playbook(
    playbook_id: UUID,
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Get a playbook with all its versions."""
    pb = await store.get_playbook(session, playbook_id)
    if pb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playbook not found")
    versions = await store.get_versions(session, playbook_id)
    return PlaybookDetailRead(
        id=pb.id,
        project_id=pb.project_id,
        name=pb.name,
        description=pb.description,
        owner_team=pb.owner_team,
        created_by=pb.created_by,
        created_at=pb.created_at,
        updated_at=pb.updated_at,
        versions=[PlaybookVersionRead.model_validate(v) for v in versions],
    )


@router.get(
    "/{playbook_id}/versions/{version_id}",
    response_model=PlaybookVersionRead,
)
async def get_version(
    playbook_id: UUID,
    version_id: UUID,
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Get a specific playbook version with its full spec."""
    v = await store.get_version(session, version_id)
    if v is None or v.playbook_id != playbook_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    return v


@router.post(
    "/{playbook_id}/versions",
    response_model=PlaybookVersionRead,
    status_code=status.HTTP_201_CREATED,
)
async def save_version(
    playbook_id: UUID,
    body: PlaybookVersionCreate,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("editor")),
):
    """Save a new draft version of a playbook (editor+)."""
    pb = await store.get_playbook(session, playbook_id)
    if pb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playbook not found")
    v = await store.save_version(
        session,
        playbook_id,
        workflow_spec=body.workflow_spec,
        trigger_bindings=body.trigger_bindings,
        change_notes=body.change_notes,
        created_by=user.get("sub"),
    )
    await session.commit()
    return v


@router.patch(
    "/{playbook_id}/versions/{version_id}/status",
    response_model=StatusTransitionResponse,
)
async def transition_status(
    playbook_id: UUID,
    version_id: UUID,
    body: StatusTransitionRequest,
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_role("operator")),
):
    """Transition a playbook version's status (operator+).

    Valid transitions: draft→generated/validated, generated→validated,
    validated→approved, approved→published/archived, published→archived.
    """
    v = await store.get_version(session, version_id)
    if v is None or v.playbook_id != playbook_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")

    current = PlaybookVersionStatus(v.status)
    allowed = VALID_STATUS_TRANSITIONS.get(current, [])
    if body.new_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Cannot transition from '{current.value}' to '{body.new_status.value}'. "
                f"Allowed: {[s.value for s in allowed]}"
            ),
        )

    updated = await store.transition_version_status(session, version_id, body.new_status.value)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    await session.commit()
    return StatusTransitionResponse(version_id=updated.id, new_status=body.new_status)


@router.delete("/{playbook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_playbook(
    playbook_id: UUID,
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_role("admin")),
):
    """Delete a playbook and all its versions (admin only)."""
    ok = await store.delete_playbook(session, playbook_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Playbook not found")
    await session.commit()
