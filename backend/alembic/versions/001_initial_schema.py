"""Initial schema — all tables from backend_architecture.md §5.

Revision ID: 001
Revises: None
Create Date: 2026-04-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Extensions (§5.11) ---
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # --- users (§5.10) ---
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(256), unique=True, nullable=False),
        sa.Column("display_name", sa.String(256), nullable=True),
        sa.Column("role", sa.String(32), nullable=False, server_default="viewer"),
        sa.Column("hashed_password", sa.String(256), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # --- tools (§5.1) ---
    op.create_table(
        "tools",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("input_schema", JSONB(), nullable=False),
        sa.Column("output_schema", JSONB(), nullable=False),
        sa.Column("side_effect_level", sa.String(32), nullable=False, server_default="read"),
        sa.Column("required_approvals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("environments_allowed", ARRAY(sa.Text()), nullable=False, server_default="{production,staging,development}"),
        sa.Column("embedding_text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_tools_name", "tools", ["name"])
    op.create_index("idx_tools_category", "tools", ["category"])

    # --- playbooks (§5.2) ---
    op.create_table(
        "playbooks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_team", sa.String(128), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # --- playbook_versions (§5.3) ---
    op.create_table(
        "playbook_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("playbook_id", UUID(as_uuid=True), sa.ForeignKey("playbooks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("trigger_bindings", JSONB(), nullable=True),
        sa.Column("workflow_spec", JSONB(), nullable=False),
        sa.Column("spec_checksum", sa.String(64), nullable=False),
        sa.Column("approval_info", JSONB(), nullable=True),
        sa.Column("compatibility_metadata", JSONB(), nullable=True),
        sa.Column("embedding_text", sa.Text(), nullable=True),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("change_notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("playbook_id", "version_number"),
    )
    op.create_index("idx_playbook_versions_playbook_id", "playbook_versions", ["playbook_id"])
    op.create_index("idx_playbook_versions_status", "playbook_versions", ["status"])

    # --- trigger_rules (§5.4) ---
    op.create_table(
        "trigger_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("incident_type", sa.String(256), nullable=False),
        sa.Column("entity_filter", JSONB(), nullable=True),
        sa.Column("playbook_version_id", UUID(as_uuid=True), sa.ForeignKey("playbook_versions.id"), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_trigger_rules_incident_type", "trigger_rules", ["incident_type"])

    # --- incidents (§5.5) ---
    op.create_table(
        "incidents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("incident_key", sa.String(512), unique=True, nullable=False),
        sa.Column("incident_type", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("severity", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("entity", JSONB(), nullable=False),
        sa.Column("sources", ARRAY(sa.Text()), nullable=False),
        sa.Column("evidence", JSONB(), nullable=False),
        sa.Column("playbook_version_id", UUID(as_uuid=True), sa.ForeignKey("playbook_versions.id"), nullable=True),
        sa.Column("temporal_workflow_id", sa.String(256), nullable=True),
        sa.Column("temporal_run_id", sa.String(256), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_incidents_incident_key", "incidents", ["incident_key"])
    op.create_index("idx_incidents_status", "incidents", ["status"])
    op.create_index("idx_incidents_type", "incidents", ["incident_type"])
    op.create_index("idx_incidents_created_at", "incidents", ["created_at"], postgresql_using="btree")

    # --- incident_events (§5.6) ---
    op.create_table(
        "incident_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("incident_id", UUID(as_uuid=True), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("actor", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_incident_events_incident_id", "incident_events", ["incident_id"])
    op.create_index("idx_incident_events_created_at", "incident_events", ["created_at"], postgresql_using="btree")

    # --- classifier_outputs (§5.7) ---
    op.create_table(
        "classifier_outputs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("entity_key", sa.String(512), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence", JSONB(), nullable=True),
        sa.Column("severity_suggestion", sa.String(16), nullable=True),
        sa.Column("incident_id", UUID(as_uuid=True), sa.ForeignKey("incidents.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_classifier_outputs_entity_key", "classifier_outputs", ["entity_key"])
    op.create_index("idx_classifier_outputs_created_at", "classifier_outputs", ["created_at"], postgresql_using="btree")

    # --- approval_requests (§5.8) ---
    op.create_table(
        "approval_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("incident_id", UUID(as_uuid=True), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("workflow_id", sa.String(256), nullable=False),
        sa.Column("step_name", sa.String(128), nullable=False),
        sa.Column("requested_action", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("decided_by", sa.String(128), nullable=True),
        sa.Column("decision_notes", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_approval_requests_incident_id", "approval_requests", ["incident_id"])

    # --- alert_rules (§5.9) ---
    op.create_table(
        "alert_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("rule_type", sa.String(32), nullable=False),
        sa.Column("rule_definition", JSONB(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("alert_rules")
    op.drop_table("approval_requests")
    op.drop_table("classifier_outputs")
    op.drop_table("incident_events")
    op.drop_table("incidents")
    op.drop_table("trigger_rules")
    op.drop_table("playbook_versions")
    op.drop_table("playbooks")
    op.drop_table("tools")
    op.drop_table("users")
    op.execute("DROP EXTENSION IF EXISTS vector")
    op.execute('DROP EXTENSION IF EXISTS "uuid-ossp"')
