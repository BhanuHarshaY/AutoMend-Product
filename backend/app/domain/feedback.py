"""Pydantic schemas for the `model_feedback` table.

Table added by migration 005. Polymorphic approve/reject log keyed by
(model, target_type, target_id). No routes yet — these schemas are here
so whoever wires the review UX later has ready-to-use request/response
shapes.

Canonical vocabulary (enforced at the Pydantic layer, not at the DB, so
operators can add new values without a migration):
  - model         = "classifier" | "architect" | any future string
  - target_type   = "classifier_output" | "playbook_version" | ...
  - feedback      = "approve" | "reject" | "needs_review"
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class FeedbackValue(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    NEEDS_REVIEW = "needs_review"


class ModelFeedbackCreate(BaseModel):
    model: str = Field(..., max_length=64, description="Name of the model whose output is being graded (e.g. 'classifier', 'architect').")
    target_type: str = Field(..., max_length=64, description="Kind of object the target_id points at (e.g. 'classifier_output', 'playbook_version').")
    target_id: UUID = Field(..., description="Id of the object being graded. Not a FK — app-layer resolves based on target_type.")
    feedback: FeedbackValue = Field(..., description="Verdict.")
    reason: Optional[str] = Field(None, description="Free-form explanation. Useful as a retraining hint later.")


class ModelFeedbackRead(BaseModel):
    id: UUID
    model: str
    target_type: str
    target_id: UUID
    feedback: str
    reason: Optional[str]
    created_by: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}
