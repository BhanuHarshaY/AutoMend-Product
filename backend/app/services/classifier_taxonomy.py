"""Translate the RoBERTa inference service's 7-class output to AutoMend's 14 labels.

Two tiers:

* **Tier 1** — fixed dict mapping from inference-service label name to a coarse
  core label (e.g. ``Resource_Exhaustion`` → ``failure.resource_limit``).
* **Tier 2** — log-content regex refinements that split a coarse label into
  a finer one when patterns match (e.g. ``Resource_Exhaustion`` + CUDA in logs
  → ``failure.gpu``). Regex patterns are reused from ``log_patterns.py`` so the
  stub classifier and the taxonomy refinement stay in sync.

See DECISION-021 for the rationale (lossy-but-localized compatibility shim;
removable once the model is retrained with a finer taxonomy).
"""

from __future__ import annotations

import re
from typing import Any

from app.services.log_patterns import (
    PATTERNS_BY_LABEL,
    SEVERITY_BY_LABEL,
    any_match,
)

# ---------------------------------------------------------------------------
# Tier 1 — fixed label mapping (7 → 14, coarse)
# ---------------------------------------------------------------------------

INFERENCE_TO_CORE: dict[str, str] = {
    "Normal":              "normal",
    "Resource_Exhaustion": "failure.resource_limit",  # refined in Tier 2
    "System_Crash":        "failure.crash",
    "Network_Failure":     "failure.network",         # refined in Tier 2
    "Data_Drift":          "anomaly.pattern",
    "Auth_Failure":        "failure.authentication",
    "Permission_Denied":   "failure.authentication",  # collapsed; split later if needed
}

# Fallback when an unrecognised inference label arrives (e.g. after a model
# retrain that added a new class and nobody updated this dict).
DEFAULT_UNKNOWN_LABEL = "anomaly.pattern"


# ---------------------------------------------------------------------------
# Tier 2 — log-content refinement rules
# ---------------------------------------------------------------------------

# Structure: coarse_label → list of (finer_label, extra_regex_patterns). The
# finer_label's own PATTERNS_BY_LABEL entry is ALSO consulted. An empty
# ``extra_patterns`` list means "use only the finer label's standard patterns."
#
# Evaluated in order — first match wins. If nothing matches, the coarse label
# is returned unchanged.

REFINEMENTS: dict[str, list[tuple[str, list[re.Pattern]]]] = {
    "failure.resource_limit": [
        # GPU / CUDA signals → failure.gpu
        ("failure.gpu", []),
        # Memory-exhaustion signals → failure.memory
        ("failure.memory", []),
        # Disk / storage signals → failure.storage
        ("failure.storage", []),
    ],
    "failure.network": [
        # Upstream / 5xx signals → failure.dependency
        ("failure.dependency", []),
    ],
    # Auth_Failure and Permission_Denied both land on failure.authentication
    # in Tier 1; no split here yet. Add a REFINEMENT entry when the core
    # grows a distinct failure.permission label.
}


def refine_label(coarse_label: str, logs: list[dict[str, Any]]) -> str:
    """Return the finer core label, or ``coarse_label`` if nothing refines."""
    rules = REFINEMENTS.get(coarse_label)
    if not rules:
        return coarse_label
    for finer_label, extra_patterns in rules:
        patterns = list(PATTERNS_BY_LABEL.get(finer_label, [])) + list(extra_patterns)
        if any_match(patterns, logs):
            return finer_label
    return coarse_label


# ---------------------------------------------------------------------------
# Top-level translator
# ---------------------------------------------------------------------------

def translate_inference_output(
    resp: dict[str, Any],
    logs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Convert an inference-service response to the core 14-label shape.

    If ``resp`` is already in the core shape (i.e. it has no ``class_id``
    field), it is returned unchanged — this keeps the stub classifier path
    working without a config flag. The WindowWorker always calls the
    ClassifierClient the same way; only the downstream service differs.
    """
    # Inference-service responses carry ``class_id`` AND ``confidence_score``.
    # The stub classifier's responses carry ``confidence`` + ``evidence`` +
    # ``severity_suggestion``. Detect by the presence of ``class_id``.
    if "class_id" not in resp:
        return resp

    inference_label = str(resp.get("label", ""))
    coarse = INFERENCE_TO_CORE.get(inference_label, DEFAULT_UNKNOWN_LABEL)
    core_label = refine_label(coarse, logs)

    severity = SEVERITY_BY_LABEL.get(core_label, "medium")
    confidence = float(resp.get("confidence_score", 0.0))

    # Best-effort evidence: first five non-blank log bodies from the window.
    # The inference service doesn't return evidence today; this gives the rest
    # of the pipeline something to surface in the UI + audit trail.
    evidence: list[str] = []
    for log in logs:
        body = str(log.get("body", "")).strip()
        if body:
            evidence.append(body)
        if len(evidence) >= 5:
            break

    return {
        "label": core_label,
        "confidence": confidence,
        "evidence": evidence,
        "severity_suggestion": severity,
        "secondary_labels": [],
    }
