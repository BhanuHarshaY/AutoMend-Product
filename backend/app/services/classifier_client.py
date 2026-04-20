"""HTTP client for Model 1 classifier service (§10.4).

Calls the external classifier service via HTTP POST. Supports both
service shapes:

* The **stub** classifier at ``classifier_server.py`` posts to ``/classify``
  and returns the core's 14-label shape directly
  (``{label, confidence, evidence, severity_suggestion, secondary_labels}``).
* The **real RoBERTa** service at ``inference_backend/ClassifierModel/`` posts
  to ``/predict_anomaly`` and returns the 7-class inference shape
  (``{class_id, confidence_score, label}``).

The client auto-detects based on response shape (``class_id`` present →
inference response) and runs inference responses through
``classifier_taxonomy.translate_inference_output`` to produce the core shape
before returning. This avoids a config flag and keeps the WindowWorker's
call site identical regardless of which classifier it's pointed at.

The endpoint path is configurable via ``settings.classifier_endpoint``
(default ``/classify`` for backward compatibility).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings
from app.domain.events import ClassifierInput, ClassifierOutput
from app.services.classifier_taxonomy import translate_inference_output

logger = logging.getLogger(__name__)


class ClassifierClient:
    """Calls the external classifier service via HTTP."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: int | None = None,
        endpoint: str | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = base_url or settings.classifier_service_url
        self.timeout = timeout or settings.classifier_timeout_seconds
        self.endpoint = endpoint or getattr(settings, "classifier_endpoint", "/classify")

    async def classify(self, input_data: dict | ClassifierInput) -> dict:
        """POST to the classifier service and return the core-shape result.

        Accepts either a raw dict or a ``ClassifierInput`` model. Always
        returns the 14-label core shape — inference-service responses are
        translated via ``classifier_taxonomy.translate_inference_output``.
        """
        if isinstance(input_data, ClassifierInput):
            payload = input_data.model_dump()
        else:
            payload = input_data

        url = f"{self.base_url}{self.endpoint}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            raw: dict[str, Any] = response.json()

        logs = payload.get("logs", []) if isinstance(payload, dict) else []
        return translate_inference_output(raw, logs)

    async def classify_typed(self, input_data: ClassifierInput) -> ClassifierOutput:
        """Typed version — returns a validated ClassifierOutput model."""
        result = await self.classify(input_data)
        return ClassifierOutput.model_validate(result)
