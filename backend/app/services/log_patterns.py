"""Shared regex patterns for AutoMend's 14-label taxonomy.

Two consumers:

1. The stub classifier (``app/services/classifier_server.py``) iterates ``PATTERNS``
   in order and picks the label with the most matches.
2. The inference-taxonomy translator (``app/services/classifier_taxonomy.py``)
   uses ``PATTERNS_BY_LABEL`` to refine coarse labels from the RoBERTa service
   (e.g. ``Resource_Exhaustion`` + CUDA/OOM/disk logs → ``failure.gpu`` /
   ``failure.memory`` / ``failure.storage``).

Keeping both consumers on the same patterns prevents drift between stub and
inference paths. See DECISION-021.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Patterns — ordered list of (label, severity, compiled regex list).
# Order matters for the stub classifier (first label wins on ties). The
# taxonomy refinement layer uses PATTERNS_BY_LABEL below and is order-agnostic.
# ---------------------------------------------------------------------------

PATTERNS: list[tuple[str, str, list[re.Pattern]]] = [
    (
        "failure.memory",
        "high",
        [
            re.compile(r"(?i)out\s*of\s*memory|OOM|oom.kill|memory\s+alloc", re.IGNORECASE),
            re.compile(r"(?i)CUDA\s+error.*memory|cannot\s+allocate\s+memory", re.IGNORECASE),
        ],
    ),
    (
        "failure.gpu",
        "high",
        [
            re.compile(r"(?i)GPU\s+error|Xid|ECC\s+error|NVML|DCGM|gpu.*fault", re.IGNORECASE),
            re.compile(r"(?i)CUDA\s+error(?!.*memory)|nvidia-smi.*failed", re.IGNORECASE),
        ],
    ),
    (
        "failure.crash",
        "high",
        [
            re.compile(r"(?i)segfault|SIGSEGV|panic|fatal\s+error|unhandled\s+exception", re.IGNORECASE),
            re.compile(r"(?i)core\s+dumped|abort|SIGABRT", re.IGNORECASE),
        ],
    ),
    (
        "failure.network",
        "medium",
        [
            re.compile(r"(?i)connection\s+(refused|reset|timed?\s*out)|ECONNREFUSED", re.IGNORECASE),
            re.compile(r"(?i)DNS\s+(resolution|lookup)\s+fail|no\s+route\s+to\s+host", re.IGNORECASE),
        ],
    ),
    (
        "failure.dependency",
        "medium",
        [
            re.compile(r"(?i)upstream\s+(unavailable|timeout)|502\s+bad\s+gateway|503\s+service", re.IGNORECASE),
            re.compile(r"(?i)dependency\s+(fail|error|unavailable)", re.IGNORECASE),
        ],
    ),
    (
        "failure.authentication",
        "medium",
        [
            re.compile(r"(?i)401\s+unauthorized|403\s+forbidden|token\s+expired", re.IGNORECASE),
            re.compile(r"(?i)auth(entication|orization)\s+(fail|denied|error)", re.IGNORECASE),
        ],
    ),
    (
        "failure.storage",
        "high",
        [
            re.compile(r"(?i)disk\s+(full|I/O\s+error)|read.only\s+file\s*system", re.IGNORECASE),
            re.compile(r"(?i)volume\s+mount\s+fail|PVC.*pending|no\s+space\s+left", re.IGNORECASE),
        ],
    ),
    (
        "failure.configuration",
        "medium",
        [
            re.compile(r"(?i)config.*error|missing\s+env|invalid\s+(config|flag)", re.IGNORECASE),
            re.compile(r"(?i)parse\s+error.*config|YAML.*error", re.IGNORECASE),
        ],
    ),
    (
        "failure.resource_limit",
        "medium",
        [
            re.compile(r"(?i)CPU\s+throttl|evict|quota\s+exceeded|resource\s+limit", re.IGNORECASE),
            re.compile(r"(?i)OOMKilled|memory\s+limit|cgroup", re.IGNORECASE),
        ],
    ),
    (
        "failure.deployment",
        "medium",
        [
            re.compile(r"(?i)image\s+pull\s+(fail|error|back.off)|ErrImagePull", re.IGNORECASE),
            re.compile(r"(?i)rollout\s+fail|CrashLoopBackOff|CreateContainerError", re.IGNORECASE),
        ],
    ),
    (
        "degradation.latency",
        "medium",
        [
            re.compile(r"(?i)high\s+latency|slow\s+query|request\s+timeout|p99.*exceed", re.IGNORECASE),
            re.compile(r"(?i)deadline\s+exceeded|context\s+deadline", re.IGNORECASE),
        ],
    ),
    (
        "degradation.throughput",
        "low",
        [
            re.compile(r"(?i)throughput\s+(drop|low|degrad)|queue\s+backlog", re.IGNORECASE),
            re.compile(r"(?i)rate\s+limit|429\s+too\s+many", re.IGNORECASE),
        ],
    ),
    (
        "anomaly.pattern",
        "low",
        [
            re.compile(r"(?i)unusual|unexpected|anomal", re.IGNORECASE),
        ],
    ),
]

# Derived lookups ------------------------------------------------------------

PATTERNS_BY_LABEL: dict[str, list[re.Pattern]] = {
    label: patterns for label, _, patterns in PATTERNS
}

SEVERITY_BY_LABEL: dict[str, str] = {label: severity for label, severity, _ in PATTERNS}
# "normal" has no pattern but still needs a severity for translation output.
SEVERITY_BY_LABEL.setdefault("normal", "info")


def any_match(patterns: list[re.Pattern], logs: list[dict[str, Any]], max_logs: int = 50) -> bool:
    """True iff any of ``patterns`` matches any log body (bounded scan)."""
    body = "\n".join(str(log.get("body", "")) for log in logs[:max_logs])
    return any(p.search(body) for p in patterns)
