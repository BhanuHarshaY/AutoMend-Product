"""Add model_feedback table.

Polymorphic approve/reject log keyed by (model, target_type, target_id).
Used later as a retraining signal — sample rejects + reasons, relabel,
fine-tune the classifier / architect on the resulting pairs.

Deliberately NOT wired to UI or CRUD yet — the schema is in place so the
app can start INSERTing rows whenever review UX lands. Reads can happen
via psql in the meantime.

Revision ID: 005
Revises: 004
Create Date: 2026-04-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_feedback",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=False),
        sa.Column("feedback", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Common query shapes:
    #  - "all feedback on this specific prediction"
    #      → (target_type, target_id)
    #  - "all rejects for the classifier since last retrain"
    #      → (model, feedback)
    #  - "recent feedback in reverse chrono"
    #      → created_at DESC
    op.create_index(
        "idx_model_feedback_target",
        "model_feedback",
        ["target_type", "target_id"],
    )
    op.create_index(
        "idx_model_feedback_model_feedback",
        "model_feedback",
        ["model", "feedback"],
    )
    op.create_index(
        "idx_model_feedback_created_at",
        "model_feedback",
        [sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_model_feedback_created_at", table_name="model_feedback")
    op.drop_index("idx_model_feedback_model_feedback", table_name="model_feedback")
    op.drop_index("idx_model_feedback_target", table_name="model_feedback")
    op.drop_table("model_feedback")
