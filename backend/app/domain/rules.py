"""Alert rule and trigger rule domain models (§5.4, §5.9)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Alert rules (§5.9)
# ---------------------------------------------------------------------------


class AlertRuleType(str, Enum):
    PROMETHEUS = "prometheus"
    CLASSIFIER_THRESHOLD = "classifier_threshold"
    COMPOSITE = "composite"


class AlertRuleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    rule_type: AlertRuleType
    rule_definition: dict
    severity: str = "medium"
    is_active: bool = True


class AlertRuleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    rule_type: Optional[AlertRuleType] = None
    rule_definition: Optional[dict] = None
    severity: Optional[str] = None
    is_active: Optional[bool] = None


class AlertRuleRead(BaseModel):
    id: UUID
    name: str
    description: Optional[str]
    rule_type: AlertRuleType
    rule_definition: dict
    severity: str
    is_active: bool
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Trigger rules (§5.4)
# ---------------------------------------------------------------------------


class TriggerRuleCreate(BaseModel):
    incident_type: str
    entity_filter: Optional[dict] = None
    playbook_version_id: UUID
    priority: int = 0
    is_active: bool = True


class TriggerRuleRead(BaseModel):
    id: UUID
    incident_type: str
    entity_filter: Optional[dict]
    playbook_version_id: UUID
    priority: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
