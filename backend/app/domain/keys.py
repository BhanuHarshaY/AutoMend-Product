"""Entity key and incident key builders (§9.5)."""

from __future__ import annotations

DEFAULT_KEY_TEMPLATE = "{cluster}/{namespace}/{service}"

SUPPORTED_KEY_TEMPLATES = [
    "{cluster}/{namespace}/{pod}",
    "{cluster}/{namespace}/{service}",
    "{service}/{tenant}/{region}",
    "{node}/{gpu_id}/{workload}",
    "{cluster}/{service}/{deployment}",
]


def build_entity_key(
    attributes: dict,
    template: str = DEFAULT_KEY_TEMPLATE,
) -> str:
    """Build entity key from log attributes using the configured template."""
    try:
        return template.format(**attributes)
    except KeyError:
        # Fallback: use whatever attributes are available
        parts = []
        for field in ["cluster", "namespace", "service", "pod"]:
            if field in attributes and attributes[field]:
                parts.append(attributes[field])
        return "/".join(parts) if parts else "unknown"


def build_incident_key(entity_key: str, failure_label: str) -> str:
    """Build incident dedup key from entity key + classification label."""
    return f"{entity_key}/{failure_label}"
