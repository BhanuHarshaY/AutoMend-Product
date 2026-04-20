"""Workflow execution status routes (§23.2).

GET    /api/workflows                       — List active/recent workflow executions
GET    /api/workflows/{workflow_id}          — Get workflow execution detail
POST   /api/workflows/{workflow_id}/signal   — Send a signal to a running workflow
POST   /api/workflows/{workflow_id}/cancel   — Cancel a running workflow
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from temporalio.client import Client as TemporalClient
from temporalio.service import RPCError

from app.dependencies import get_current_user, get_temporal_client, require_role

router = APIRouter()


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class WorkflowExecutionSummary(BaseModel):
    workflow_id: str
    run_id: str
    workflow_type: str
    status: str
    start_time: Optional[str] = None
    close_time: Optional[str] = None


class WorkflowExecutionDetail(BaseModel):
    workflow_id: str
    run_id: str
    workflow_type: str
    status: str
    task_queue: str
    start_time: Optional[str] = None
    close_time: Optional[str] = None
    execution_time: Optional[str] = None
    history_length: int = 0
    memo: dict = {}
    search_attributes: dict = {}


class SignalRequest(BaseModel):
    signal_name: str
    payload: dict = {}


class MessageResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _status_name(s: Any) -> str:
    """Convert a Temporal WorkflowExecutionStatus enum to a readable string."""
    return str(s.name) if hasattr(s, "name") else str(s)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[WorkflowExecutionSummary])
async def list_workflows(
    query: str = Query(
        'WorkflowType = "DynamicPlaybookExecutor"',
        description="Temporal visibility query",
    ),
    limit: int = Query(50, ge=1, le=200),
    temporal: TemporalClient = Depends(get_temporal_client),
    _user: dict = Depends(get_current_user),
):
    """List active/recent workflow executions from Temporal."""
    results: list[WorkflowExecutionSummary] = []
    count = 0
    async for wf in temporal.list_workflows(query=query, limit=limit):
        results.append(
            WorkflowExecutionSummary(
                workflow_id=wf.id,
                run_id=wf.run_id or "",
                workflow_type=wf.workflow_type or "",
                status=_status_name(wf.status),
                start_time=wf.start_time.isoformat() if wf.start_time else None,
                close_time=wf.close_time.isoformat() if wf.close_time else None,
            )
        )
        count += 1
        if count >= limit:
            break
    return results


@router.get("/{workflow_id:path}", response_model=WorkflowExecutionDetail)
async def get_workflow(
    workflow_id: str,
    temporal: TemporalClient = Depends(get_temporal_client),
    _user: dict = Depends(get_current_user),
):
    """Get workflow execution detail from Temporal."""
    handle = temporal.get_workflow_handle(workflow_id)
    try:
        desc = await handle.describe()
    except RPCError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found: {e}",
        )

    # memo and search_attributes are async methods in the Temporal SDK
    try:
        memo_raw = await desc.memo() if callable(desc.memo) else (desc.memo or {})
    except Exception:
        memo_raw = {}
    try:
        search_attrs_raw = (
            await desc.search_attributes() if callable(desc.search_attributes)
            else (desc.search_attributes or {})
        )
    except Exception:
        search_attrs_raw = {}

    return WorkflowExecutionDetail(
        workflow_id=desc.id,
        run_id=desc.run_id or "",
        workflow_type=desc.workflow_type or "",
        status=_status_name(desc.status),
        task_queue=desc.task_queue,
        start_time=desc.start_time.isoformat() if desc.start_time else None,
        close_time=desc.close_time.isoformat() if desc.close_time else None,
        execution_time=desc.execution_time.isoformat() if desc.execution_time else None,
        history_length=desc.history_length,
        memo={k: str(v) for k, v in (memo_raw or {}).items()},
        search_attributes={k: str(v) for k, v in (search_attrs_raw or {}).items()},
    )


@router.post("/{workflow_id:path}/signal", response_model=MessageResponse)
async def signal_workflow(
    workflow_id: str,
    body: SignalRequest,
    temporal: TemporalClient = Depends(get_temporal_client),
    _user: dict = Depends(require_role("operator")),
):
    """Send a signal to a running workflow (operator+)."""
    handle = temporal.get_workflow_handle(workflow_id)
    try:
        await handle.signal(body.signal_name, body.payload)
    except RPCError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found or not running: {e}",
        )
    return MessageResponse(message=f"Signal '{body.signal_name}' sent to workflow '{workflow_id}'")


@router.post("/{workflow_id:path}/cancel", response_model=MessageResponse)
async def cancel_workflow(
    workflow_id: str,
    temporal: TemporalClient = Depends(get_temporal_client),
    _user: dict = Depends(require_role("operator")),
):
    """Cancel a running workflow (operator+)."""
    handle = temporal.get_workflow_handle(workflow_id)
    try:
        await handle.cancel()
    except RPCError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found or already completed: {e}",
        )
    return MessageResponse(message=f"Cancel requested for workflow '{workflow_id}'")
