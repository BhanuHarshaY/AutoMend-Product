"""Inference pipeline for the Track A RoBERTa classifier.

The service now accepts raw log entries and tokenizes them internally with the
stock RoBERTa tokenizer. The previous sequence-id / decile-bucket vocabulary
from the training pipeline has been removed — see DECISION-019 in the core
repo's DECISIONS.md for the rationale.
"""

from __future__ import annotations

from typing import Any

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

# Tokenizer config — must match the RoBERTa base used at training time.
MAX_LENGTH = 512
BODY_SEPARATOR = "\n"


def logs_to_text(logs: list[dict[str, Any]], max_logs: int) -> str:
    """Concatenate log bodies into a single string for tokenization.

    Takes at most ``max_logs`` entries. Each entry's ``body`` field is used;
    missing/non-string bodies are coerced to str. The tokenizer's
    ``truncation=True`` handles length — we do not pre-truncate here.
    """
    bodies: list[str] = []
    for entry in logs[:max_logs]:
        body = entry.get("body", "")
        if not isinstance(body, str):
            body = str(body)
        body = body.strip()
        if body:
            bodies.append(body)
    return BODY_SEPARATOR.join(bodies)


def run_inference(
    model: AutoModelForSequenceClassification,
    tokenizer: AutoTokenizer,
    logs: list[dict[str, Any]],
    max_logs: int,
    device: torch.device,
) -> tuple[int, float]:
    """Run one forward pass and return (class_id, confidence_score).

    Empty windows (no usable log bodies) are classified as class 0 ("Normal")
    with confidence 1.0 — there's nothing anomalous to see.
    """
    text = logs_to_text(logs, max_logs)
    if not text:
        return 0, 1.0

    encoding = tokenizer(
        text,
        return_tensors="pt",
        max_length=MAX_LENGTH,
        padding="max_length",
        truncation=True,
    )

    input_ids = encoding["input_ids"].to(device)
    attention_mask = encoding["attention_mask"].to(device)

    with torch.no_grad():
        outputs = model(input_ids=input_ids, attention_mask=attention_mask)

    probs = torch.softmax(outputs.logits, dim=-1).squeeze(0)
    class_id = int(probs.argmax().item())
    confidence_score = float(probs[class_id].item())

    return class_id, confidence_score
