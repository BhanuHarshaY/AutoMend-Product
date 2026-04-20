"""Incident routes (§23.2).

GET    /api/incidents                — List incidents with filters
GET    /api/incidents/stats          — Aggregate stats
GET    /api/incidents/{id}           — Get incident detail
PATCH  /api/incidents/{id}           — Update incident (status, severity)
POST   /api/incidents/{id}/acknowledge — Acknowledge
POST   /api/incidents/{id}/resolve     — Manually resolve
GET    /api/incidents/{id}/events      — Event timeline
GET    /api/incidents/{id}/workflow     — Associated workflow status
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db, require_role
from app.stores import postgres_store as store

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class IncidentRead(BaseModel):
    id: UUID
    incident_key: str
    incident_type: str
    status: str
    severity: str
    entity: dict
    sources: list[str]
    evidence: dict
    playbook_version_id: Optional[UUID] = None
    temporal_workflow_id: Optional[str] = None
    temporal_run_id: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class IncidentUpdate(BaseModel):
    status: Optional[str] = None
    severity: Optional[str] = None


class IncidentEventRead(BaseModel):
    id: UUID
    incident_id: UUID
    event_type: str
    payload: dict
    actor: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class IncidentStatsResponse(BaseModel):
    by_status: dict[str, int]
    by_severity: dict[str, int]


class WorkflowStatusResponse(BaseModel):
    temporal_workflow_id: Optional[str] = None
    temporal_run_id: Optional[str] = None
    status: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


# NOTE: /stats must be registered BEFORE /{id} so FastAPI doesn't treat
# "stats" as a UUID path parameter.
@router.get("/stats", response_model=IncidentStatsResponse)
async def get_stats(
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Aggregate counts by status and severity."""
    return await store.get_incident_stats(session)


@router.get("", response_model=list[IncidentRead])
async def list_incidents(
    status_filter: str | None = Query(None, alias="status", description="Filter by status"),
    severity: str | None = Query(None, description="Filter by severity"),
    incident_type: str | None = Query(None, description="Filter by incident type"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """List incidents with optional filters."""
    return await store.list_incidents(
        session,
        status=status_filter,
        severity=severity,
        incident_type=incident_type,
        limit=limit,
        offset=offset,
    )


@router.get("/{incident_id}", response_model=IncidentRead)
async def get_incident(
    incident_id: UUID,
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Get incident detail."""
    inc = await store.get_incident(session, incident_id)
    if inc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return inc


@router.patch("/{incident_id}", response_model=IncidentRead)
async def update_incident(
    incident_id: UUID,
    body: IncidentUpdate,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("operator")),
):
    """Update incident status and/or severity (operator+)."""
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields to update",
        )
    inc = await store.update_incident(session, incident_id, **updates)
    if inc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    # Record event
    await store.add_event(
        session, incident_id, "status_changed", updates, actor=user.get("sub", "system")
    )
    await session.commit()
    return inc


@router.post("/{incident_id}/acknowledge", response_model=IncidentRead)
async def acknowledge_incident(
    incident_id: UUID,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("operator")),
):
    """Acknowledge an incident (operator+)."""
    inc = await store.update_incident(session, incident_id, status="acknowledged")
    if inc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    await store.add_event(
        session, incident_id, "status_changed",
        {"status": "acknowledged"}, actor=user.get("sub", "system"),
    )
    await session.commit()
    return inc


@router.post("/{incident_id}/resolve", response_model=IncidentRead)
async def resolve_incident(
    incident_id: UUID,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("operator")),
):
    """Manually resolve an incident (operator+)."""
    inc = await store.resolve_incident(session, incident_id)
    if inc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    await store.add_event(
        session, incident_id, "status_changed",
        {"status": "resolved"}, actor=user.get("sub", "system"),
    )
    await session.commit()
    return inc


@router.get("/{incident_id}/events", response_model=list[IncidentEventRead])
async def get_events(
    incident_id: UUID,
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Get the event timeline for an incident."""
    # Verify incident exists
    inc = await store.get_incident(session, incident_id)
    if inc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return await store.get_incident_events(session, incident_id)


@router.get("/{incident_id}/workflow", response_model=WorkflowStatusResponse)
async def get_workflow_status(
    incident_id: UUID,
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Get the associated workflow status for an incident.

    Returns the Temporal workflow/run IDs and the incident status.
    Full Temporal workflow details are available via /api/workflows/{id}.
    """
    inc = await store.get_incident(session, incident_id)
    if inc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return WorkflowStatusResponse(
        temporal_workflow_id=inc.temporal_workflow_id,
        temporal_run_id=inc.temporal_run_id,
        status=inc.status,
    )
