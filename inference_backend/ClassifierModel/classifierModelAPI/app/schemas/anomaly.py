"""
Pydantic schemas for the Model 1 anomaly classification endpoint.

Request:  A 5-minute window of raw log entries (matching the AutoMend core
          backend's `classifier_input` dict from app/workers/window_worker.py).
          The service tokenizes the log bodies internally using the stock
          RoBERTa tokenizer — the caller does NOT need to pre-encode.

Response: The predicted anomaly class (0-6) with its confidence score and
          a human-readable label. The core backend translates this 7-class
          taxonomy into its own 14-label taxonomy (see
          `backend/app/services/classifier_taxonomy.py` in the core repo).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Maps class index to the label used during training (track_a.yaml)
LABEL_NAMES: dict[int, str] = {
    0: "Normal",
    1: "Resource_Exhaustion",
    2: "System_Crash",
    3: "Network_Failure",
    4: "Data_Drift",
    5: "Auth_Failure",
    6: "Permission_Denied",
}


class AnomalyRequest(BaseModel):
    """Incoming prediction request — a 5-minute window of raw logs.

    Shape matches the core backend's WindowWorker output
    (`backend/app/workers/window_worker.py`, ``classifier_input`` dict).
    Extra keys are accepted and ignored to stay tolerant of schema drift
    on the caller side.
    """

    model_config = ConfigDict(extra="ignore")

    entity_key: str = Field(
        default="",
        description="Canonical entity key (cluster/namespace/workload) the logs belong to.",
    )
    window_start: str = Field(
        default="",
        description="ISO-8601 timestamp marking the window start (informational).",
    )
    window_end: str = Field(
        default="",
        description="ISO-8601 timestamp marking the window end (informational).",
    )
    logs: list[dict[str, Any]] = Field(
        ...,
        description=(
            "List of log entries. Each entry should have a 'body' field with the raw "
            "log line (other fields like 'attributes' are ignored)."
        ),
        examples=[[
            {"body": "CUDA error: out of memory", "attributes": {}},
            {"body": "pod evicted due to memory pressure", "attributes": {}},
        ]],
    )
    max_logs: int = Field(
        default=200,
        ge=1,
        description="Cap on how many log entries to consume from the window.",
    )
    entity_context: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional context dict (e.g., k8s attributes) — currently unused by the model.",
    )

    @field_validator("logs")
    @classmethod
    def must_not_be_empty(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(v) == 0:
            raise ValueError("logs must contain at least one entry")
        return v


class AnomalyResponse(BaseModel):
    """Prediction result returned by the /predict_anomaly endpoint."""

    class_id: int = Field(
        ...,
        ge=0,
        le=6,
        description="Predicted anomaly class (0 = Normal, 1-6 = anomaly types).",
    )
    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Softmax probability of the predicted class.",
    )
    label: str = Field(
        ...,
        description="Human-readable name of the predicted class.",
        examples=["Normal", "Resource_Exhaustion"],
    )
