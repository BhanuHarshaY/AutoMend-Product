"""Projects: bind to namespace + add playbooks_enabled kill switch.

Task 11.8c. After Task 11.8b's Clusters API + Task 11.8d's UI, each project
becomes the frontend view of a single Kubernetes namespace. This migration
wires the schema to match:

  - Adds `namespace` TEXT column on projects (nullable initially for backfill).
  - Backfills existing rows by slugifying `name` + appending the first 4 chars
    of `id` so collisions are impossible. For dev data the resulting namespace
    strings (e.g. "ml-platform-a3bf") are cosmetic — operators re-assign them
    to real namespaces via a future update endpoint.
  - Promotes the column to NOT NULL + UNIQUE.
  - Adds `playbooks_enabled` BOOLEAN defaulting to true. CorrelationWorker
    consults this to decide whether to start a Temporal workflow for a new
    incident in the project's namespace.
  - Drops `status` column. It was display-only (active / paused / draft) with
    no execution semantics; `playbooks_enabled` replaces the only meaningful
    use case (pause automation without deleting trigger rules). See
    DECISION-028.
  - Drops the `idx_projects_status` index.

Revision ID: 003
Revises: 002
Create Date: 2026-04-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- namespace column (nullable initially, to allow backfill) ---
    op.add_column("projects", sa.Column("namespace", sa.Text(), nullable=True))

    # Backfill every existing row. Slugify the project name into a DNS-1123-ish
    # string, then append the first 4 chars of the UUID for guaranteed
    # uniqueness (prevents the UNIQUE constraint from failing on dev data
    # where two projects happen to share a name).
    op.execute(
        """
        UPDATE projects
        SET namespace = lower(
            regexp_replace(coalesce(name, 'project'), '[^a-zA-Z0-9]+', '-', 'g')
        ) || '-' || substring(id::text, 1, 4)
        WHERE namespace IS NULL
        """
    )

    # Trim leading/trailing dashes introduced by the regex (e.g. "  ml  " →
    # "-ml-" → "ml"). Done as a second pass so the concat above stays simple.
    op.execute(
        """
        UPDATE projects
        SET namespace = trim(both '-' from namespace)
        WHERE namespace LIKE '-%' OR namespace LIKE '%-'
        """
    )

    # Promote to NOT NULL + UNIQUE.
    op.alter_column("projects", "namespace", nullable=False)
    op.create_unique_constraint("uq_projects_namespace", "projects", ["namespace"])

    # --- playbooks_enabled kill switch ---
    op.add_column(
        "projects",
        sa.Column(
            "playbooks_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )

    # --- drop status column + its index ---
    op.drop_index("idx_projects_status", table_name="projects")
    op.drop_column("projects", "status")


def downgrade() -> None:
    # Recreate status column with the original shape.
    op.add_column(
        "projects",
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="draft",
        ),
    )
    op.create_index("idx_projects_status", "projects", ["status"])

    # Drop the new columns in reverse order of creation.
    op.drop_column("projects", "playbooks_enabled")
    op.drop_constraint("uq_projects_namespace", "projects", type_="unique")
    op.drop_column("projects", "namespace")
