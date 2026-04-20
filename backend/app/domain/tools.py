"""Tool registry domain models (§17).

Pydantic models for API input/output. The ORM model is in app/models/db.py.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class SideEffectLevel(str, Enum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class ToolCreate(BaseModel):
    """Input for creating a new tool."""

    name: str
    display_name: str
    description: str
    category: str
    input_schema: dict
    output_schema: dict
    side_effect_level: SideEffectLevel = SideEffectLevel.READ
    required_approvals: int = 0
    environments_allowed: list[str] = ["production", "staging", "development"]


class ToolUpdate(BaseModel):
    """Input for updating an existing tool (all fields optional)."""

    display_name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    input_schema: Optional[dict] = None
    output_schema: Optional[dict] = None
    side_effect_level: Optional[SideEffectLevel] = None
    required_approvals: Optional[int] = None
    environments_allowed: Optional[list[str]] = None
    is_active: Optional[bool] = None


class ToolRead(BaseModel):
    """Output for reading a tool."""

    id: UUID
    name: str
    display_name: str
    description: str
    category: str
    input_schema: dict
    output_schema: dict
    side_effect_level: SideEffectLevel
    required_approvals: int
    environments_allowed: list[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ToolSearchResult(BaseModel):
    """A tool returned from vector search with a relevance score."""

    id: UUID
    name: str
    description: str
    relevance_score: float
    input_schema: dict
    side_effect_level: SideEffectLevel
