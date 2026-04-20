"""Playbook and DSL domain models (§18, §19).

Pydantic models representing the playbook lifecycle and the workflow DSL
that the DynamicPlaybookExecutor interprets at runtime.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Playbook lifecycle (§18)
# ---------------------------------------------------------------------------


class PlaybookVersionStatus(str, Enum):
    DRAFT = "draft"
    GENERATED = "generated"
    VALIDATED = "validated"
    APPROVED = "approved"
    PUBLISHED = "published"
    ARCHIVED = "archived"


VALID_STATUS_TRANSITIONS: dict[PlaybookVersionStatus, list[PlaybookVersionStatus]] = {
    PlaybookVersionStatus.DRAFT: [PlaybookVersionStatus.GENERATED, PlaybookVersionStatus.VALIDATED],
    PlaybookVersionStatus.GENERATED: [PlaybookVersionStatus.VALIDATED],
    PlaybookVersionStatus.VALIDATED: [PlaybookVersionStatus.APPROVED],
    PlaybookVersionStatus.APPROVED: [PlaybookVersionStatus.PUBLISHED, PlaybookVersionStatus.ARCHIVED],
    PlaybookVersionStatus.PUBLISHED: [PlaybookVersionStatus.ARCHIVED],
    PlaybookVersionStatus.ARCHIVED: [],
}


class PlaybookCreate(BaseModel):
    name: str
    description: Optional[str] = None
    owner_team: Optional[str] = None
    project_id: Optional[UUID] = None


class PlaybookRead(BaseModel):
    id: UUID
    project_id: Optional[UUID] = None
    name: str
    description: Optional[str]
    owner_team: Optional[str]
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PlaybookVersionCreate(BaseModel):
    workflow_spec: dict
    trigger_bindings: Optional[dict] = None
    change_notes: Optional[str] = None


class PlaybookVersionRead(BaseModel):
    id: UUID
    playbook_id: UUID
    version_number: int
    status: PlaybookVersionStatus
    trigger_bindings: Optional[dict]
    workflow_spec: dict
    spec_checksum: str
    change_notes: Optional[str]
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Playbook DSL types (§19)
# ---------------------------------------------------------------------------


class StepType(str, Enum):
    ACTION = "action"
    APPROVAL = "approval"
    CONDITION = "condition"
    DELAY = "delay"
    PARALLEL = "parallel"
    NOTIFICATION = "notification"
    SUB_PLAYBOOK = "sub_playbook"


class RetryConfig(BaseModel):
    max_attempts: int = 1
    backoff: str = "fixed"  # "fixed" | "exponential"
    initial_interval: Optional[str] = None
    max_interval: Optional[str] = None


class StepBranches(BaseModel):
    true: Optional[str] = None  # noqa: A003
    false: Optional[str] = None  # noqa: A003


class PlaybookStep(BaseModel):
    """A single step in the playbook DSL (§19.1)."""

    id: str  # noqa: A003
    name: str
    type: StepType  # noqa: A003
    tool: Optional[str] = None
    input: Optional[dict] = None  # noqa: A003
    timeout: Optional[str] = None
    retry: Optional[RetryConfig] = None
    on_success: Optional[str] = None
    on_failure: Optional[str] = None
    # Condition step
    condition: Optional[str] = None
    branches: Optional[StepBranches] = None
    # Delay step
    duration: Optional[str] = None
    # Parallel step
    parallel_steps: Optional[list[str]] = None
    # Approval step
    approval_channel: Optional[str] = None
    approval_message: Optional[str] = None
    approval_timeout: Optional[str] = None


class CompletionAction(BaseModel):
    resolve_incident: bool = True
    notification: Optional[dict] = None


class AbortAction(BaseModel):
    escalate: bool = True
    page_oncall: bool = False
    notification: Optional[dict] = None


class PlaybookTrigger(BaseModel):
    incident_types: list[str]
    severity_filter: Optional[list[str]] = None
    entity_filter: Optional[dict] = None


class PlaybookSpec(BaseModel):
    """The full playbook DSL specification (§19.1)."""

    name: str
    description: Optional[str] = None
    version: str
    trigger: PlaybookTrigger
    parameters: Optional[dict] = None
    steps: list[PlaybookStep]
    on_complete: Optional[CompletionAction] = None
    on_abort: Optional[AbortAction] = None
