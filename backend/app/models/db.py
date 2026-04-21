"""SQLAlchemy 2.0 declarative ORM models for the AutoMend database.

All tables from backend_architecture.md §5 are defined here.
pgvector columns use the Vector type from the pgvector library.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Base class for all ORM models."""


# ---------------------------------------------------------------------------
# 5.10  users
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer")
    hashed_password: Mapped[str | None] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# 5.1  tools
# ---------------------------------------------------------------------------


class Tool(Base):
    __tablename__ = "tools"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    input_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    output_schema: Mapped[dict] = mapped_column(JSONB, nullable=False)
    side_effect_level: Mapped[str] = mapped_column(
        String(32), nullable=False, default="read"
    )
    required_approvals: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    environments_allowed: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=lambda: ["production", "staging", "development"],
        server_default="{production,staging,development}",
    )
    embedding_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(1536), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_tools_name", "name"),
        Index("idx_tools_category", "category"),
    )


# ---------------------------------------------------------------------------
# 5.2  playbooks
# ---------------------------------------------------------------------------


class Project(Base):
    """A project groups related playbooks (e.g., 'Classification Service' owns
    playbooks for GPU OOM recovery, accuracy drop, latency alerts, etc.).

    Added in Phase 9.2 — not in backend_architecture.md. See DECISION-017.
    """

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    # Task 11.8c — each project binds to exactly one Kubernetes namespace.
    # UNIQUE so the CorrelationWorker can safely look up a project by the
    # incident's entity.namespace (DNS-1123 labels are ≤253 chars).
    namespace: Mapped[str] = mapped_column(String(253), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    # Kill switch consulted by CorrelationWorker before it starts a Temporal
    # workflow. False = create the incident for visibility but don't execute
    # any playbook. Replaces the former status enum (DECISION-028).
    playbooks_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    owner_team: Mapped[str | None] = mapped_column(String(128))
    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    playbooks: Mapped[list[Playbook]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Playbook(Base):
    __tablename__ = "playbooks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,  # Existing playbooks may not have a project (backfill is a separate migration)
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    owner_team: Mapped[str | None] = mapped_column(String(128))
    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    project: Mapped[Project | None] = relationship(back_populates="playbooks")
    versions: Mapped[list[PlaybookVersion]] = relationship(
        back_populates="playbook", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_playbooks_project_id", "project_id"),
    )


# ---------------------------------------------------------------------------
# 5.3  playbook_versions
# ---------------------------------------------------------------------------


class PlaybookVersion(Base):
    __tablename__ = "playbook_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    playbook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("playbooks.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    trigger_bindings: Mapped[dict | None] = mapped_column(JSONB)
    workflow_spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    spec_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_info: Mapped[dict | None] = mapped_column(JSONB)
    compatibility_metadata: Mapped[dict | None] = mapped_column(JSONB)
    embedding_text: Mapped[str | None] = mapped_column(Text)
    embedding = mapped_column(Vector(1536), nullable=True)
    change_notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    playbook: Mapped[Playbook] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint("playbook_id", "version_number"),
        Index("idx_playbook_versions_playbook_id", "playbook_id"),
        Index("idx_playbook_versions_status", "status"),
    )


# ---------------------------------------------------------------------------
# 5.4  trigger_rules
# ---------------------------------------------------------------------------


class TriggerRule(Base):
    __tablename__ = "trigger_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    incident_type: Mapped[str] = mapped_column(String(256), nullable=False)
    entity_filter: Mapped[dict | None] = mapped_column(JSONB)
    playbook_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        # ON DELETE CASCADE — a trigger_rule without its target version is
        # dead weight (migration 004).
        ForeignKey("playbook_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    playbook_version: Mapped[PlaybookVersion] = relationship()

    __table_args__ = (
        Index("idx_trigger_rules_incident_type", "incident_type"),
        Index(
            "idx_trigger_rules_active",
            "is_active",
            postgresql_where=(is_active == True),  # noqa: E712
        ),
    )


# ---------------------------------------------------------------------------
# 5.5  incidents
# ---------------------------------------------------------------------------


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    incident_key: Mapped[str] = mapped_column(
        String(512), unique=True, nullable=False
    )
    incident_type: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    entity: Mapped[dict] = mapped_column(JSONB, nullable=False)
    sources: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    playbook_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # ON DELETE SET NULL — incidents are historical records; they
        # should outlive the playbook that remediated them (migration 004).
        ForeignKey("playbook_versions.id", ondelete="SET NULL"),
    )
    temporal_workflow_id: Mapped[str | None] = mapped_column(String(256))
    temporal_run_id: Mapped[str | None] = mapped_column(String(256))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    events: Mapped[list[IncidentEvent]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_incidents_incident_key", "incident_key"),
        Index("idx_incidents_status", "status"),
        Index("idx_incidents_type", "incident_type"),
        Index("idx_incidents_created_at", created_at.desc()),
    )


# ---------------------------------------------------------------------------
# 5.6  incident_events
# ---------------------------------------------------------------------------


class IncidentEvent(Base):
    __tablename__ = "incident_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    actor: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    incident: Mapped[Incident] = relationship(back_populates="events")

    __table_args__ = (
        Index("idx_incident_events_incident_id", "incident_id"),
        Index("idx_incident_events_created_at", created_at.desc()),
    )


# ---------------------------------------------------------------------------
# 5.7  classifier_outputs
# ---------------------------------------------------------------------------


class ClassifierOutput(Base):
    __tablename__ = "classifier_outputs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    entity_key: Mapped[str] = mapped_column(String(512), nullable=False)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    severity_suggestion: Mapped[str | None] = mapped_column(String(16))
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_classifier_outputs_entity_key", "entity_key"),
        Index("idx_classifier_outputs_created_at", created_at.desc()),
    )


# ---------------------------------------------------------------------------
# 5.8  approval_requests
# ---------------------------------------------------------------------------


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False
    )
    workflow_id: Mapped[str] = mapped_column(String(256), nullable=False)
    step_name: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_action: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(128))
    decision_notes: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "idx_approval_requests_status",
            "status",
            postgresql_where=(status == "pending"),
        ),
        Index("idx_approval_requests_incident_id", "incident_id"),
    )


# ---------------------------------------------------------------------------
# 5.9  alert_rules
# ---------------------------------------------------------------------------


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


# ---------------------------------------------------------------------------
# 5.10  model_feedback — approve/reject signals on per-prediction outputs.
# Polymorphic target so one table covers both models:
#   model="classifier", target_type="classifier_output", target_id → classifier_outputs.id
#   model="architect",  target_type="playbook_version",  target_id → playbook_versions.id
# target_id is NOT a FK (polymorphic). Enforcement is app-layer.
# ---------------------------------------------------------------------------


class ModelFeedback(Base):
    __tablename__ = "model_feedback"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Which model produced the output being graded. Free-form string so we
    # can add new models (e.g. "embedding", "retrain-2026q2") without a
    # schema change.
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    # What kind of object the target_id points at. Free-form for the same
    # reason; app-layer knows the mapping.
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # "approve" | "reject" (app-layer validated; no Postgres enum so we can
    # grow the vocabulary — e.g. "needs_review" later — without a migration).
    feedback: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # Common query shape: "all feedback on this specific prediction"
        Index("idx_model_feedback_target", "target_type", "target_id"),
        # Retraining sample query: "all rejects for the classifier since T"
        Index("idx_model_feedback_model_feedback", "model", "feedback"),
        Index("idx_model_feedback_created_at", created_at.desc()),
    )
