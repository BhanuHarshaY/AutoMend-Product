"""Canonical incident model used throughout the system (§12)."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class IncidentStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
    SUPPRESSED = "suppressed"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class EntityInfo(BaseModel):
    cluster: Optional[str] = None
    namespace: Optional[str] = None
    service: Optional[str] = None
    pod: Optional[str] = None
    node: Optional[str] = None
    container: Optional[str] = None
    gpu_id: Optional[str] = None
    tenant: Optional[str] = None
    region: Optional[str] = None
    deployment: Optional[str] = None


class ClassifierEvidence(BaseModel):
    label: str
    confidence: float
    evidence_lines: list[str] = []
    severity_suggestion: Optional[str] = None


class IncidentEvidence(BaseModel):
    metric_alerts: list[str] = []
    classifier: Optional[ClassifierEvidence] = None
    raw_signals: list[dict] = []


class CanonicalIncident(BaseModel):
    """The canonical incident object used throughout the system."""

    id: UUID = Field(default_factory=uuid4)
    incident_key: str
    incident_type: str
    status: IncidentStatus = IncidentStatus.OPEN
    severity: Severity = Severity.MEDIUM
    entity: EntityInfo
    entity_key: str
    sources: list[str]
    evidence: IncidentEvidence
    playbook_version_id: Optional[UUID] = None
    temporal_workflow_id: Optional[str] = None
    temporal_run_id: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
