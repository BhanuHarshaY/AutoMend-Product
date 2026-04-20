"""Postgres store — async CRUD operations for all domain entities.

Every public method receives an ``AsyncSession`` so the caller (route handler
or service) controls the transaction boundary via FastAPI's ``get_db``
dependency.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import (
    AlertRule,
    ApprovalRequest,
    ClassifierOutput,
    Incident,
    IncidentEvent,
    Playbook,
    PlaybookVersion,
    Project,
    Tool,
    TriggerRule,
    User,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _spec_checksum(workflow_spec: dict) -> str:
    raw = json.dumps(workflow_spec, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


# ===================================================================
# TOOLS
# ===================================================================


async def create_tool(session: AsyncSession, **kwargs: Any) -> Tool:
    if "embedding_text" not in kwargs:
        kwargs["embedding_text"] = (
            f"{kwargs.get('name', '')} {kwargs.get('description', '')} "
            f"{kwargs.get('category', '')}"
        )
    tool = Tool(**kwargs)
    session.add(tool)
    await session.flush()
    return tool


async def get_tool(session: AsyncSession, tool_id: UUID) -> Tool | None:
    return await session.get(Tool, tool_id)


async def get_tool_by_name(session: AsyncSession, name: str) -> Tool | None:
    result = await session.execute(select(Tool).where(Tool.name == name))
    return result.scalar_one_or_none()


async def list_tools(
    session: AsyncSession,
    *,
    active_only: bool = True,
    category: str | None = None,
) -> Sequence[Tool]:
    stmt = select(Tool)
    if active_only:
        stmt = stmt.where(Tool.is_active == True)  # noqa: E712
    if category:
        stmt = stmt.where(Tool.category == category)
    stmt = stmt.order_by(Tool.name)
    result = await session.execute(stmt)
    return result.scalars().all()


async def update_tool(
    session: AsyncSession, tool_id: UUID, **kwargs: Any
) -> Tool | None:
    tool = await session.get(Tool, tool_id)
    if tool is None:
        return None
    for k, v in kwargs.items():
        setattr(tool, k, v)
    tool.updated_at = _utcnow()
    await session.flush()
    return tool


async def deactivate_tool(session: AsyncSession, tool_id: UUID) -> bool:
    tool = await session.get(Tool, tool_id)
    if tool is None:
        return False
    tool.is_active = False
    tool.updated_at = _utcnow()
    await session.flush()
    return True


# ===================================================================
# PROJECTS (Phase 9.2 — DECISION-017)
# ===================================================================


async def create_project(session: AsyncSession, **kwargs: Any) -> Project:
    project = Project(**kwargs)
    session.add(project)
    await session.flush()
    return project


async def get_project(session: AsyncSession, project_id: UUID) -> Project | None:
    return await session.get(Project, project_id)


async def get_project_by_namespace(
    session: AsyncSession, namespace: str
) -> Project | None:
    """Task 11.8c — lookup by the UNIQUE namespace column.

    CorrelationWorker uses this to consult `playbooks_enabled` before
    starting a Temporal workflow for a new incident.
    """
    stmt = select(Project).where(Project.namespace == namespace)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_projects(
    session: AsyncSession, *, enabled: bool | None = None
) -> Sequence[Project]:
    stmt = select(Project)
    if enabled is not None:
        stmt = stmt.where(Project.playbooks_enabled == enabled)
    stmt = stmt.order_by(Project.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()


async def update_project(
    session: AsyncSession, project_id: UUID, **kwargs: Any
) -> Project | None:
    project = await session.get(Project, project_id)
    if project is None:
        return None
    for k, v in kwargs.items():
        setattr(project, k, v)
    project.updated_at = _utcnow()
    await session.flush()
    return project


async def delete_project(session: AsyncSession, project_id: UUID) -> bool:
    project = await session.get(Project, project_id)
    if project is None:
        return False
    await session.delete(project)
    await session.flush()
    return True


async def list_playbooks_by_project(
    session: AsyncSession, project_id: UUID
) -> Sequence[Playbook]:
    stmt = (
        select(Playbook)
        .where(Playbook.project_id == project_id)
        .order_by(Playbook.created_at.desc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()


# ===================================================================
# PLAYBOOKS
# ===================================================================


async def create_playbook(session: AsyncSession, **kwargs: Any) -> Playbook:
    pb = Playbook(**kwargs)
    session.add(pb)
    await session.flush()
    return pb


async def get_playbook(session: AsyncSession, playbook_id: UUID) -> Playbook | None:
    return await session.get(Playbook, playbook_id)


async def list_playbooks(session: AsyncSession) -> Sequence[Playbook]:
    result = await session.execute(select(Playbook).order_by(Playbook.created_at.desc()))
    return result.scalars().all()


async def delete_playbook(session: AsyncSession, playbook_id: UUID) -> bool:
    pb = await session.get(Playbook, playbook_id)
    if pb is None:
        return False
    await session.delete(pb)
    await session.flush()
    return True


# ===================================================================
# PLAYBOOK VERSIONS
# ===================================================================


async def save_version(
    session: AsyncSession,
    playbook_id: UUID,
    workflow_spec: dict,
    *,
    trigger_bindings: dict | None = None,
    change_notes: str | None = None,
    created_by: str | None = None,
) -> PlaybookVersion:
    # Determine next version number
    stmt = (
        select(func.coalesce(func.max(PlaybookVersion.version_number), 0))
        .where(PlaybookVersion.playbook_id == playbook_id)
    )
    result = await session.execute(stmt)
    next_version = result.scalar_one() + 1

    pv = PlaybookVersion(
        playbook_id=playbook_id,
        version_number=next_version,
        workflow_spec=workflow_spec,
        spec_checksum=_spec_checksum(workflow_spec),
        trigger_bindings=trigger_bindings,
        change_notes=change_notes,
        created_by=created_by,
    )
    session.add(pv)
    await session.flush()
    return pv


async def get_version(
    session: AsyncSession, version_id: UUID
) -> PlaybookVersion | None:
    return await session.get(PlaybookVersion, version_id)


async def get_versions(
    session: AsyncSession, playbook_id: UUID
) -> Sequence[PlaybookVersion]:
    stmt = (
        select(PlaybookVersion)
        .where(PlaybookVersion.playbook_id == playbook_id)
        .order_by(PlaybookVersion.version_number.desc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def transition_version_status(
    session: AsyncSession,
    version_id: UUID,
    new_status: str,
) -> PlaybookVersion | None:
    pv = await session.get(PlaybookVersion, version_id)
    if pv is None:
        return None
    old_status = pv.status
    pv.status = new_status
    pv.updated_at = _utcnow()
    await session.flush()

    # Task 11.8e — when a version becomes 'published', repoint every active
    # trigger_rule that currently targets a DIFFERENT version of the SAME
    # playbook to this new one. Keeps operators from having to UPDATE
    # trigger_rules by hand after every publish (see DECISION-026 follow-up).
    # Idempotent: re-publishing the already-published version is a no-op
    # because the old → new check filters out rules already on `pv.id`.
    if new_status == "published" and old_status != "published":
        repointed = await _repoint_trigger_rules_to_version(session, pv)
        if repointed:
            logger.info(
                "Repointed %d active trigger rule(s) for playbook %s onto newly-published version %s",
                repointed, pv.playbook_id, pv.id,
            )

    return pv


async def _repoint_trigger_rules_to_version(
    session: AsyncSession,
    pv: PlaybookVersion,
) -> int:
    """Set `trigger_rules.playbook_version_id` to `pv.id` for every active
    rule that currently targets a sibling version of the same playbook.

    Returns the number of rows updated. Rules for OTHER playbooks are
    untouched — repointing is scoped to this playbook's own version family.
    """
    sibling_version_ids = select(PlaybookVersion.id).where(
        PlaybookVersion.playbook_id == pv.playbook_id,
        PlaybookVersion.id != pv.id,
    )
    stmt = (
        update(TriggerRule)
        .where(
            TriggerRule.is_active == True,  # noqa: E712
            TriggerRule.playbook_version_id.in_(sibling_version_ids),
        )
        .values(
            playbook_version_id=pv.id,
            updated_at=_utcnow(),
        )
    )
    result = await session.execute(stmt)
    # rowcount is exposed on the underlying CursorResult for DML statements.
    # mypy's async Result stub doesn't advertise it; cast accordingly.
    return getattr(result, "rowcount", 0) or 0


# ===================================================================
# TRIGGER RULES
# ===================================================================


async def create_trigger_rule(session: AsyncSession, **kwargs: Any) -> TriggerRule:
    rule = TriggerRule(**kwargs)
    session.add(rule)
    await session.flush()
    return rule


async def list_trigger_rules(
    session: AsyncSession, *, active_only: bool = True
) -> Sequence[TriggerRule]:
    stmt = select(TriggerRule)
    if active_only:
        stmt = stmt.where(TriggerRule.is_active == True)  # noqa: E712
    stmt = stmt.order_by(TriggerRule.priority.desc())
    result = await session.execute(stmt)
    return result.scalars().all()


async def find_playbook_for_incident(
    session: AsyncSession,
    incident_type: str,
) -> TriggerRule | None:
    """Find the highest-priority active trigger rule for an incident type."""
    stmt = (
        select(TriggerRule)
        .where(
            TriggerRule.incident_type == incident_type,
            TriggerRule.is_active == True,  # noqa: E712
        )
        .order_by(TriggerRule.priority.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def deactivate_trigger_rule(session: AsyncSession, rule_id: UUID) -> bool:
    rule = await session.get(TriggerRule, rule_id)
    if rule is None:
        return False
    rule.is_active = False
    rule.updated_at = _utcnow()
    await session.flush()
    return True


# ===================================================================
# INCIDENTS
# ===================================================================


async def create_incident(session: AsyncSession, **kwargs: Any) -> Incident:
    incident = Incident(**kwargs)
    session.add(incident)
    await session.flush()
    return incident


async def get_incident(session: AsyncSession, incident_id: UUID) -> Incident | None:
    return await session.get(Incident, incident_id)


async def get_incident_by_key(
    session: AsyncSession, incident_key: str
) -> Incident | None:
    result = await session.execute(
        select(Incident).where(Incident.incident_key == incident_key)
    )
    return result.scalar_one_or_none()


async def list_incidents(
    session: AsyncSession,
    *,
    status: str | None = None,
    severity: str | None = None,
    incident_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Sequence[Incident]:
    stmt = select(Incident)
    if status:
        stmt = stmt.where(Incident.status == status)
    if severity:
        stmt = stmt.where(Incident.severity == severity)
    if incident_type:
        stmt = stmt.where(Incident.incident_type == incident_type)
    stmt = stmt.order_by(Incident.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return result.scalars().all()


async def update_incident(
    session: AsyncSession, incident_id: UUID, **kwargs: Any
) -> Incident | None:
    incident = await session.get(Incident, incident_id)
    if incident is None:
        return None
    for k, v in kwargs.items():
        setattr(incident, k, v)
    incident.updated_at = _utcnow()
    await session.flush()
    return incident


async def resolve_incident(session: AsyncSession, incident_id: UUID) -> Incident | None:
    return await update_incident(
        session, incident_id, status="resolved", resolved_at=_utcnow()
    )


async def get_incident_stats(session: AsyncSession) -> dict[str, Any]:
    """Return aggregate counts by status and severity."""
    # By status
    status_stmt = (
        select(Incident.status, func.count())
        .group_by(Incident.status)
    )
    status_result = await session.execute(status_stmt)
    by_status = {row[0]: row[1] for row in status_result.all()}

    # By severity
    severity_stmt = (
        select(Incident.severity, func.count())
        .group_by(Incident.severity)
    )
    severity_result = await session.execute(severity_stmt)
    by_severity = {row[0]: row[1] for row in severity_result.all()}

    return {"by_status": by_status, "by_severity": by_severity}


# ===================================================================
# INCIDENT EVENTS
# ===================================================================


async def add_event(
    session: AsyncSession,
    incident_id: UUID,
    event_type: str,
    payload: dict,
    actor: str = "system",
) -> IncidentEvent:
    evt = IncidentEvent(
        incident_id=incident_id,
        event_type=event_type,
        payload=payload,
        actor=actor,
    )
    session.add(evt)
    await session.flush()
    return evt


async def get_incident_events(
    session: AsyncSession, incident_id: UUID
) -> Sequence[IncidentEvent]:
    stmt = (
        select(IncidentEvent)
        .where(IncidentEvent.incident_id == incident_id)
        .order_by(IncidentEvent.created_at)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


# ===================================================================
# CLASSIFIER OUTPUTS
# ===================================================================


async def create_classifier_output(
    session: AsyncSession, **kwargs: Any
) -> ClassifierOutput:
    co = ClassifierOutput(**kwargs)
    session.add(co)
    await session.flush()
    return co


# ===================================================================
# APPROVAL REQUESTS
# ===================================================================


async def create_approval_request(
    session: AsyncSession, **kwargs: Any
) -> ApprovalRequest:
    ar = ApprovalRequest(**kwargs)
    session.add(ar)
    await session.flush()
    return ar


async def get_approval_request(
    session: AsyncSession, request_id: UUID
) -> ApprovalRequest | None:
    return await session.get(ApprovalRequest, request_id)


async def decide_approval(
    session: AsyncSession,
    request_id: UUID,
    decision: str,
    decided_by: str,
    notes: str | None = None,
) -> ApprovalRequest | None:
    ar = await session.get(ApprovalRequest, request_id)
    if ar is None:
        return None
    ar.status = decision
    ar.decided_by = decided_by
    ar.decision_notes = notes
    ar.decided_at = _utcnow()
    await session.flush()
    return ar


# ===================================================================
# ALERT RULES
# ===================================================================


async def create_alert_rule(session: AsyncSession, **kwargs: Any) -> AlertRule:
    rule = AlertRule(**kwargs)
    session.add(rule)
    await session.flush()
    return rule


async def get_alert_rule(
    session: AsyncSession, rule_id: UUID
) -> AlertRule | None:
    return await session.get(AlertRule, rule_id)


async def list_alert_rules(
    session: AsyncSession, *, active_only: bool = False
) -> Sequence[AlertRule]:
    stmt = select(AlertRule)
    if active_only:
        stmt = stmt.where(AlertRule.is_active == True)  # noqa: E712
    stmt = stmt.order_by(AlertRule.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()


async def update_alert_rule(
    session: AsyncSession, rule_id: UUID, **kwargs: Any
) -> AlertRule | None:
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        return None
    for k, v in kwargs.items():
        setattr(rule, k, v)
    rule.updated_at = _utcnow()
    await session.flush()
    return rule


async def delete_alert_rule(session: AsyncSession, rule_id: UUID) -> bool:
    rule = await session.get(AlertRule, rule_id)
    if rule is None:
        return False
    await session.delete(rule)
    await session.flush()
    return True


# ===================================================================
# USERS
# ===================================================================


async def create_user(session: AsyncSession, **kwargs: Any) -> User:
    user = User(**kwargs)
    session.add(user)
    await session.flush()
    return user


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user(session: AsyncSession, user_id: UUID) -> User | None:
    return await session.get(User, user_id)
