"""Internal event schemas — classified events, signals, and classifier I/O.

Classified log events (§9.4), internal signal schema (§11.3),
classifier input/output (§10.2–10.4).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.domain.incidents import EntityInfo


# ---------------------------------------------------------------------------
# Classifier I/O (§10.2–10.4)
# ---------------------------------------------------------------------------


class LogEntry(BaseModel):
    """A single normalized log entry sent to the classifier."""

    timestamp: str
    body: str
    severity: str = "INFO"
    attributes: dict = {}


class ClassifierInput(BaseModel):
    """Request body for POST /classify (§10.2)."""

    entity_key: str
    window_start: str
    window_end: str
    logs: list[LogEntry]
    max_logs: int = 200
    entity_context: dict = {}


class SecondaryLabel(BaseModel):
    label: str
    confidence: float


class ClassifierOutput(BaseModel):
    """Response body from POST /classify (§10.2)."""

    label: str
    confidence: float
    evidence: list[str]
    severity_suggestion: Optional[str] = None
    secondary_labels: list[SecondaryLabel] = []


# ---------------------------------------------------------------------------
# Classified Log Event (§9.4)
# ---------------------------------------------------------------------------


class WindowInfo(BaseModel):
    start: str
    end: str
    log_count: int


class ClassificationInfo(BaseModel):
    label: str
    confidence: float
    evidence: list[str]
    severity_suggestion: Optional[str] = None


class ClassifiedLogEvent(BaseModel):
    """Event produced by the window-worker after classification (§9.4)."""

    event_id: UUID = Field(default_factory=uuid4)
    event_type: str = "classified_log_event"
    entity_key: str
    entity: EntityInfo
    classification: ClassificationInfo
    window: WindowInfo
    timestamp: datetime


# ---------------------------------------------------------------------------
# Internal Signal Schema (§11.3)
# ---------------------------------------------------------------------------


class SignalType(str, Enum):
    CLASSIFIER_OUTPUT = "classifier_output"
    PROMETHEUS_ALERT = "prometheus_alert"
    APP_EVENT = "app_event"
    MANUAL_TRIGGER = "manual_trigger"


class InternalSignal(BaseModel):
    """Canonical signal consumed by the correlation worker (§11.3)."""

    signal_id: UUID = Field(default_factory=uuid4)
    signal_type: SignalType
    source: str
    entity_key: str
    entity: EntityInfo
    incident_type_hint: str
    severity: str = "medium"
    payload: dict = {}
    timestamp: datetime
