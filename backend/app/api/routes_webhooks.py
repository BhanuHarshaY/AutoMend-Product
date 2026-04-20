"""Webhook routes for external integrations (§24).

POST /api/webhooks/alertmanager  — Receive Alertmanager notifications
POST /api/webhooks/ingest/otlp   — Receive OTLP log export
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis

from app.dependencies import get_redis
from app.domain.keys import build_entity_key
from app.stores import redis_store as rs

router = APIRouter()


# ---------------------------------------------------------------------------
# Alertmanager transform (§11.4)
# ---------------------------------------------------------------------------


def transform_alertmanager_alert(alert: dict) -> dict:
    """Transform an Alertmanager alert to the internal signal format."""
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})

    entity: dict[str, str] = {}
    for field in ["cluster", "namespace", "service", "pod", "node", "gpu_id"]:
        value = labels.get(field) or annotations.get(f"entity_{field}")
        if value:
            entity[field] = value

    entity_key = build_entity_key(entity)

    return {
        "signal_id": str(uuid4()),
        "signal_type": "prometheus_alert",
        "source": "alertmanager",
        "entity_key": entity_key,
        "entity": json.dumps(entity),
        "incident_type_hint": labels.get("incident_type", "incident.unknown"),
        "severity": labels.get("severity", "medium"),
        "payload": json.dumps({
            "alert_name": labels.get("alertname"),
            "status": alert.get("status"),
            "starts_at": alert.get("startsAt"),
            "ends_at": alert.get("endsAt"),
            "generator_url": alert.get("generatorURL"),
            "summary": annotations.get("summary"),
            "labels": labels,
        }),
        "timestamp": alert.get("startsAt", datetime.now(timezone.utc).isoformat()),
    }


# ---------------------------------------------------------------------------
# OTLP log normalization
# ---------------------------------------------------------------------------


def _extract_attributes(attrs: list[dict]) -> dict[str, str]:
    """Extract key-value pairs from OTLP attribute format."""
    result: dict[str, str] = {}
    for attr in attrs:
        key = attr.get("key", "")
        value = attr.get("value", {})
        # OTLP values can be stringValue, intValue, etc.
        for vtype in ("stringValue", "intValue", "boolValue", "doubleValue"):
            if vtype in value:
                result[key] = str(value[vtype])
                break
    return result


def _normalize_log_record(record: dict, resource_attrs: dict) -> dict[str, str]:
    """Normalize a single OTLP log record for the window worker."""
    body = record.get("body", {})
    body_str = body.get("stringValue", "") if isinstance(body, dict) else str(body)
    log_attrs = _extract_attributes(record.get("attributes", []))

    merged = {**resource_attrs, **log_attrs}
    entity_key = build_entity_key(merged)

    return {
        "entity_key": entity_key,
        "timestamp": record.get("timeUnixNano", ""),
        "body": body_str,
        "severity": record.get("severityText", "INFO"),
        "attributes": json.dumps(merged),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/alertmanager")
async def alertmanager_webhook(
    request: Request,
    redis: Redis = Depends(get_redis),
):
    """Receive Alertmanager webhook notifications (§24.1).

    Transforms each alert to an internal signal and pushes
    to the correlation input stream.
    """
    payload = await request.json()
    alerts = payload.get("alerts", [])

    for alert in alerts:
        signal = transform_alertmanager_alert(alert)
        await rs.stream_add(
            redis,
            rs.STREAM_CORRELATION_INPUT,
            signal,
            maxlen=10000,
        )

    return {"status": "ok", "processed": len(alerts)}


def _normalize_flat_record(record: dict) -> dict[str, str] | None:
    """Normalize a flat log record (Fluent Bit style) for the window worker.

    Accepts records with a `log` / `message` / `body` field. Unwraps the
    nested ``kubernetes`` metadata that Fluent Bit's kubernetes filter adds
    (namespace_name, pod_name, container_name, etc.) into the top-level
    ``namespace`` / ``pod`` / ``service`` names that ``build_entity_key``
    looks for. Other scalar fields are copied through unchanged.
    """
    body_str = (
        record.get("log")
        or record.get("message")
        or record.get("body")
        or ""
    )
    if not body_str:
        return None

    attrs: dict[str, str] = {}

    # Fluent Bit's kubernetes filter nests its enrichment under a
    # "kubernetes" key by default. Flatten the common fields into the
    # names build_entity_key wants.
    kube = record.get("kubernetes") or {}
    if isinstance(kube, dict):
        if kube.get("namespace_name"):
            attrs["namespace"] = str(kube["namespace_name"])
        if kube.get("pod_name"):
            attrs["pod"] = str(kube["pod_name"])
        if kube.get("container_name"):
            attrs["service"] = str(kube["container_name"])
        if kube.get("host"):
            attrs["node"] = str(kube["host"])
        # Keep the raw kubernetes block around as one JSON string for
        # anyone downstream who wants the full labels / annotations.
        attrs["kubernetes"] = json.dumps(kube)

    # Copy remaining scalar fields (host, stream, etc.) as entity attrs.
    for k, v in record.items():
        if k in ("log", "message", "body", "kubernetes"):
            continue
        if isinstance(v, (str, int, float, bool)):
            attrs.setdefault(k, str(v))

    entity_key = build_entity_key(attrs) or "unknown"

    return {
        "entity_key": entity_key,
        "timestamp": str(record.get("date") or record.get("timestamp") or ""),
        "body": str(body_str),
        "severity": str(record.get("severity") or record.get("level") or "INFO"),
        "attributes": json.dumps(attrs),
    }


def _parse_ingest_body(body: bytes) -> tuple[list[dict], dict | None]:
    """Return (flat_records, otlp_payload) parsed from the HTTP body.

    Accepts:
      * OTLP HTTP/JSON: ``{"resourceLogs": [...]}`` → returned as otlp_payload.
      * Single flat record: ``{"log": "..."}`` → one-item list.
      * Array of flat records: ``[{"log": "..."}, ...]``.
      * NDJSON (newline-separated JSON objects).
    Returns ``([], None)`` if the body can't be parsed as any of the above.
    Protobuf bodies (e.g. Fluent Bit's ``opentelemetry`` output) are NOT
    supported — use the ``http`` output with ``Format json`` instead.
    """
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        return [], None

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # NDJSON fallback: parse each non-empty line.
        records: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    records.append(obj)
            except json.JSONDecodeError:
                continue
        return records, None

    if isinstance(data, dict) and "resourceLogs" in data:
        return [], data
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)], None
    if isinstance(data, dict):
        return [data], None
    return [], None


@router.post("/ingest/otlp")
async def otlp_ingest(
    request: Request,
    redis: Redis = Depends(get_redis),
):
    """Receive log records from an OTLP-HTTP exporter OR a flat-JSON shipper.

    Accepts:
      * OTLP HTTP/JSON (OpenTelemetry Collector, OTel SDK HTTP exporter) with
        the ``resourceLogs > scopeLogs > logRecords`` structure.
      * Flat JSON from shippers like Fluent Bit's ``http`` output with
        ``Format json`` — either a single ``{"log": "...", ...}`` object, an
        array of them, or NDJSON. Protobuf bodies (Fluent Bit's
        ``opentelemetry`` output) are not supported; use the ``http`` output
        with ``Format json`` instead. See MANUAL_TESTING.md §9.
    """
    body = await request.body()
    flat_records, otlp_payload = _parse_ingest_body(body)
    count = 0

    if otlp_payload is not None:
        for resource_log in otlp_payload.get("resourceLogs", []):
            resource_attrs = _extract_attributes(
                resource_log.get("resource", {}).get("attributes", [])
            )
            for scope_log in resource_log.get("scopeLogs", []):
                for log_record in scope_log.get("logRecords", []):
                    normalized = _normalize_log_record(log_record, resource_attrs)
                    await rs.stream_add(
                        redis,
                        rs.STREAM_NORMALIZED_LOGS,
                        normalized,
                        maxlen=50000,
                    )
                    count += 1

    for record in flat_records:
        normalized = _normalize_flat_record(record)
        if normalized is None:
            continue
        await rs.stream_add(
            redis,
            rs.STREAM_NORMALIZED_LOGS,
            normalized,
            maxlen=50000,
        )
        count += 1

    return {"status": "ok", "processed": count}
