"""Alert rules and trigger rules routes (§23.2).

GET    /api/rules                — List alert rules
POST   /api/rules                — Create alert rule
PUT    /api/rules/{id}           — Update alert rule
DELETE /api/rules/{id}           — Delete alert rule
GET    /api/rules/trigger-rules  — List trigger rules (incident→playbook mappings)
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db, require_role
from app.domain.rules import (
    AlertRuleCreate,
    AlertRuleRead,
    AlertRuleUpdate,
    TriggerRuleRead,
)
from app.stores import postgres_store as store

router = APIRouter()


# ---------------------------------------------------------------------------
# Alert rules
# ---------------------------------------------------------------------------


@router.get("/trigger-rules", response_model=list[TriggerRuleRead])
async def list_trigger_rules(
    active_only: bool = Query(True),
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """List trigger rules (incident type → playbook version mappings)."""
    return await store.list_trigger_rules(session, active_only=active_only)


@router.get("", response_model=list[AlertRuleRead])
async def list_alert_rules(
    active_only: bool = Query(False),
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """List all alert rules."""
    return await store.list_alert_rules(session, active_only=active_only)


@router.post("", response_model=AlertRuleRead, status_code=status.HTTP_201_CREATED)
async def create_alert_rule(
    body: AlertRuleCreate,
    session: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("editor")),
):
    """Create a new alert rule (editor+)."""
    rule = await store.create_alert_rule(
        session,
        name=body.name,
        description=body.description,
        rule_type=body.rule_type.value,
        rule_definition=body.rule_definition,
        severity=body.severity,
        is_active=body.is_active,
        created_by=user.get("sub"),
    )
    await session.commit()
    return rule


@router.put("/{rule_id}", response_model=AlertRuleRead)
async def update_alert_rule(
    rule_id: UUID,
    body: AlertRuleUpdate,
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_role("editor")),
):
    """Update an alert rule (editor+)."""
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields to update",
        )
    # Convert enum to string if present
    if "rule_type" in updates and updates["rule_type"] is not None:
        updates["rule_type"] = updates["rule_type"].value
    rule = await store.update_alert_rule(session, rule_id, **updates)
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert rule not found")
    await session.commit()
    return rule


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert_rule(
    rule_id: UUID,
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_role("admin")),
):
    """Delete an alert rule (admin only)."""
    ok = await store.delete_alert_rule(session, rule_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert rule not found")
    await session.commit()
