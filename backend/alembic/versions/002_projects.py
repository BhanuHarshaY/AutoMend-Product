"""Add projects table + project_id FK on playbooks.

Not in backend_architecture.md — added in Phase 9.2 to give the frontend
a first-class container for grouping related playbooks (e.g., one project
per ML service, multiple playbooks per failure mode). See DECISION-017.

Revision ID: 002
Revises: 001
Create Date: 2026-04-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- projects table ---
    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("owner_team", sa.String(128), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_projects_status", "projects", ["status"])

    # --- project_id FK on playbooks ---
    # Nullable so existing playbooks survive the migration. New playbooks will
    # always be created with a project (enforced at the API layer).
    op.add_column(
        "playbooks",
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("idx_playbooks_project_id", "playbooks", ["project_id"])


def downgrade() -> None:
    op.drop_index("idx_playbooks_project_id", table_name="playbooks")
    op.drop_column("playbooks", "project_id")
    op.drop_index("idx_projects_status", table_name="projects")
    op.drop_table("projects")
