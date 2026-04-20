"""Tool registry routes (§17, §23.2).

GET    /api/tools        — List all tools (optional category filter)
GET    /api/tools/{id}   — Get tool detail
POST   /api/tools        — Create a new tool (admin)
PUT    /api/tools/{id}   — Update tool (admin)
DELETE /api/tools/{id}   — Deactivate tool (admin)
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db, require_role
from app.domain.tools import ToolCreate, ToolRead, ToolUpdate
from app.stores import postgres_store as store

router = APIRouter()


@router.get("", response_model=list[ToolRead])
async def list_tools(
    category: str | None = Query(None, description="Filter by category"),
    active_only: bool = Query(True, description="Only return active tools"),
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """List all tools, optionally filtered by category."""
    tools = await store.list_tools(session, active_only=active_only, category=category)
    return tools


@router.get("/{tool_id}", response_model=ToolRead)
async def get_tool(
    tool_id: UUID,
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Get a single tool by ID."""
    tool = await store.get_tool(session, tool_id)
    if tool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")
    return tool


@router.post("", response_model=ToolRead, status_code=status.HTTP_201_CREATED)
async def create_tool(
    body: ToolCreate,
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_role("admin")),
):
    """Create a new tool (admin only)."""
    existing = await store.get_tool_by_name(session, body.name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tool with name '{body.name}' already exists",
        )
    tool = await store.create_tool(session, **body.model_dump())
    await session.commit()
    return tool


@router.put("/{tool_id}", response_model=ToolRead)
async def update_tool(
    tool_id: UUID,
    body: ToolUpdate,
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_role("admin")),
):
    """Update an existing tool (admin only)."""
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields to update",
        )
    tool = await store.update_tool(session, tool_id, **updates)
    if tool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")
    await session.commit()
    return tool


@router.delete("/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(
    tool_id: UUID,
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_role("admin")),
):
    """Deactivate a tool (soft delete, admin only)."""
    ok = await store.deactivate_tool(session, tool_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")
    await session.commit()
