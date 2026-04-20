"""Project domain models.

Originally added in Phase 9.2 as a grouping container (DECISION-017).
Task 11.8c binds each project to a Kubernetes namespace + adds a
kill-switch boolean that replaces the display-only status enum; see
DECISION-028.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


_NAMESPACE_DESC = (
    "Kubernetes namespace this project owns. DNS-1123 label (lowercase "
    "alphanumerics + hyphens, 1–253 chars). Must be unique across projects."
)


class ProjectCreate(BaseModel):
    name: str
    namespace: str = Field(..., min_length=1, max_length=253, description=_NAMESPACE_DESC)
    description: Optional[str] = None
    owner_team: Optional[str] = None


class ProjectUpdate(BaseModel):
    # Notably absent: `namespace`. Once assigned, a project's namespace is
    # immutable — rebinding is semantically a new project because it changes
    # which trigger rules / playbooks the project effectively owns.
    name: Optional[str] = None
    description: Optional[str] = None
    owner_team: Optional[str] = None
    playbooks_enabled: Optional[bool] = None


class ProjectRead(BaseModel):
    id: UUID
    name: str
    namespace: str
    description: Optional[str]
    playbooks_enabled: bool
    owner_team: Optional[str]
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
