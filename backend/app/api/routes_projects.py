"""Project routes.

A Project is a container for related playbooks, bound to a Kubernetes
namespace (Task 11.8c). See DECISION-017 (projects) and DECISION-028
(status → playbooks_enabled kill switch).

GET    /api/projects              — List all projects (optional ?enabled=true|false)
POST   /api/projects              — Create (editor+); requires `namespace`, 409 on conflict
GET    /api/projects/{id}         — Get project detail with its playbooks
PATCH  /api/projects/{id}         — Update metadata (editor+) or playbooks_enabled (operator+)
DELETE /api/projects/{id}         — Delete project + cascade playbooks (admin)
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db, require_role
from app.domain.playbooks import PlaybookRead
from app.domain.projects import ProjectCreate, ProjectRead, ProjectUpdate
from app.stores import postgres_store as store

router = APIRouter()


class ProjectDetailRead(ProjectRead):
    """Project with its playbooks attached."""
    playbooks: list[PlaybookRead] = []


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    enabled: bool | None = Query(
        None,
        description="Filter by playbooks_enabled. Omit for all.",
    ),
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    return await store.list_projects(session, enabled=enabled)


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("editor")),
):
    """Create a project bound to a namespace. 409 if the namespace is taken."""
    existing = await store.get_project_by_namespace(session, body.namespace)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Namespace '{body.namespace}' is already bound to project '{existing.name}'",
        )
    project = await store.create_project(
        session,
        name=body.name,
        namespace=body.namespace,
        description=body.description,
        owner_team=body.owner_team,
        created_by=user.get("sub"),
    )
    await session.commit()
    return project


@router.get("/{project_id}", response_model=ProjectDetailRead)
async def get_project(
    project_id: UUID,
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    project = await store.get_project(session, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    playbooks = await store.list_playbooks_by_project(session, project_id)
    return ProjectDetailRead(
        id=project.id,
        name=project.name,
        namespace=project.namespace,
        description=project.description,
        playbooks_enabled=project.playbooks_enabled,
        owner_team=project.owner_team,
        created_by=project.created_by,
        created_at=project.created_at,
        updated_at=project.updated_at,
        playbooks=[PlaybookRead.model_validate(p) for p in playbooks],
    )


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: UUID,
    body: ProjectUpdate,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Update project metadata or toggle the kill switch.

    - name/description/owner_team: editor+
    - playbooks_enabled: operator+ (same gate the previous status field used —
      flipping remediation on/off in production warrants operator authority).
    """
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields to update",
        )

    role_hierarchy = {"admin": 4, "operator": 3, "editor": 2, "viewer": 1}
    user_level = role_hierarchy.get(user.get("role", ""), 0)
    required_level = 3 if "playbooks_enabled" in updates else 2
    if user_level < required_level:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions",
        )

    project = await store.update_project(session, project_id, **updates)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    await session.commit()
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_role("admin")),
):
    ok = await store.delete_project(session, project_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    await session.commit()
