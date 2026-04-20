"""Classifier service — standalone FastAPI app (§10).

Exposes POST /classify. Uses a rule-based pattern matcher for v1.
An LLM-based classifier can replace the _classify_logs function later.

Run with: python -m app.services.classifier_server
     or:  uvicorn app.services.classifier_server:app --host 0.0.0.0 --port 8001
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from app.services.log_patterns import PATTERNS

app = FastAPI(title="AutoMend Classifier Service", version="1.0.0")


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class ClassifyRequest(BaseModel):
    entity_key: str
    window_start: str
    window_end: str
    logs: list[dict]
    max_logs: int = 200
    entity_context: dict = {}


class SecondaryLabel(BaseModel):
    label: str
    confidence: float


class ClassifyResponse(BaseModel):
    label: str
    confidence: float
    evidence: list[str]
    severity_suggestion: str | None = None
    secondary_labels: list[SecondaryLabel] = []


# ---------------------------------------------------------------------------
# Rule-based classifier (uses PATTERNS from log_patterns module)
# ---------------------------------------------------------------------------


def _classify_logs(request: ClassifyRequest) -> ClassifyResponse:
    """Classify a window of logs using pattern matching.

    Scores each label by counting how many log lines match its patterns.
    The label with the most matches wins.
    """
    scores: dict[str, int] = {}
    matched_lines: dict[str, list[str]] = {}
    severity_map: dict[str, str] = {}

    for label, severity, patterns in PATTERNS:
        severity_map[label] = severity
        scores[label] = 0
        matched_lines[label] = []
        for log in request.logs:
            body = log.get("body", "")
            for pat in patterns:
                if pat.search(body):
                    scores[label] += 1
                    if body not in matched_lines[label]:
                        matched_lines[label].append(body)
                    break  # One match per log per label is enough

    # Find the best label
    best_label = max(scores, key=scores.get, default="normal")
    best_score = scores.get(best_label, 0)
    total_logs = max(len(request.logs), 1)

    if best_score == 0:
        return ClassifyResponse(
            label="normal",
            confidence=0.9,
            evidence=[],
            severity_suggestion="info",
        )

    # Confidence: proportion of logs matched, scaled to 0.5–1.0 range
    raw_confidence = best_score / total_logs
    confidence = round(0.5 + raw_confidence * 0.5, 2)
    confidence = min(confidence, 0.99)

    # Evidence: top 5 matching lines
    evidence = matched_lines[best_label][:5]

    # Secondary labels (any other label with matches)
    secondary = []
    for label, score in sorted(scores.items(), key=lambda x: -x[1]):
        if label != best_label and score > 0:
            sec_conf = round(0.5 + (score / total_logs) * 0.5, 2)
            secondary.append(SecondaryLabel(label=label, confidence=min(sec_conf, 0.95)))

    return ClassifyResponse(
        label=best_label,
        confidence=confidence,
        evidence=evidence,
        severity_suggestion=severity_map.get(best_label, "medium"),
        secondary_labels=secondary[:3],
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@app.post("/classify", response_model=ClassifyResponse)
async def classify(request: ClassifyRequest) -> ClassifyResponse:
    """Classify a window of logs."""
    return _classify_logs(request)


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Run standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)
