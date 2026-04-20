"""Fix ON DELETE behavior on FKs into playbook_versions.

Surfaced during Task 11.8d manual testing: deleting a project cascades to
playbooks and tries to cascade to playbook_versions, but
  - `trigger_rules.playbook_version_id`  had no ON DELETE → RESTRICT,
  - `incidents.playbook_version_id`      had no ON DELETE → RESTRICT,
so the delete blew up with `ForeignKeyViolationError`. Operators couldn't
delete a project without hand-deleting all trigger rules + nulling out
incidents first.

Chosen semantics:
  - trigger_rules  → ON DELETE CASCADE
      A trigger_rule without its target version is a dead rule. Drop it.
  - incidents      → ON DELETE SET NULL
      Incidents are historical records — losing the playbook version
      shouldn't erase the incident or its event timeline. The column is
      already nullable; SET NULL is consistent with its existing shape.

Revision ID: 004
Revises: 003
Create Date: 2026-04-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # trigger_rules.playbook_version_id → CASCADE
    op.drop_constraint(
        "trigger_rules_playbook_version_id_fkey",
        "trigger_rules",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "trigger_rules_playbook_version_id_fkey",
        "trigger_rules",
        "playbook_versions",
        ["playbook_version_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # incidents.playbook_version_id → SET NULL
    op.drop_constraint(
        "incidents_playbook_version_id_fkey",
        "incidents",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "incidents_playbook_version_id_fkey",
        "incidents",
        "playbook_versions",
        ["playbook_version_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # Restore original no-ondelete (Postgres default = NO ACTION, effectively RESTRICT).
    op.drop_constraint(
        "incidents_playbook_version_id_fkey",
        "incidents",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "incidents_playbook_version_id_fkey",
        "incidents",
        "playbook_versions",
        ["playbook_version_id"],
        ["id"],
    )

    op.drop_constraint(
        "trigger_rules_playbook_version_id_fkey",
        "trigger_rules",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "trigger_rules_playbook_version_id_fkey",
        "trigger_rules",
        "playbook_versions",
        ["playbook_version_id"],
        ["id"],
    )
