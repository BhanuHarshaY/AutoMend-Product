"""Correlation Worker — consumes signals, correlates into incidents,
and starts remediation workflows (§11).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol
from uuid import uuid4

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.domain.keys import build_incident_key
from app.stores import postgres_store as pg
from app.stores import redis_store as rs

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Temporal client protocol — allows mocking
# ---------------------------------------------------------------------------


class TemporalClientProtocol(Protocol):
    async def start_workflow(self, workflow: str, arg: Any, *, id: str, task_queue: str) -> Any:
        ...

    def get_workflow_handle(self, workflow_id: str) -> Any:
        ...


# ---------------------------------------------------------------------------
# Signal normalization
# ---------------------------------------------------------------------------


def _normalize_classified_event(fields: dict[str, str]) -> dict[str, Any]:
    """Normalize a classified_events stream entry to internal signal format."""
    classification = json.loads(fields.get("classification", "{}"))
    entity = json.loads(fields.get("entity", "{}"))
    label = classification.get("label", "unknown")

    return {
        "signal_id": fields.get("event_id", str(uuid4())),
        "signal_type": "classifier_output",
        "source": "log_classifier",
        "entity_key": fields.get("entity_key", "unknown"),
        "entity": entity,
        "incident_type_hint": f"incident.{label}",
        "severity": classification.get("severity_suggestion", "medium"),
        "payload": {
            "classification": classification,
            "window": json.loads(fields.get("window", "{}")),
        },
        "timestamp": fields.get("timestamp", ""),
    }


def _normalize_correlation_input(fields: dict[str, str]) -> dict[str, Any]:
    """Normalize a correlation_input stream entry (already in signal format)."""
    entity = fields.get("entity", "{}")
    if isinstance(entity, str):
        try:
            entity = json.loads(entity)
        except json.JSONDecodeError:
            entity = {}

    payload = fields.get("payload", "{}")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}

    return {
        "signal_id": fields.get("signal_id", str(uuid4())),
        "signal_type": fields.get("signal_type", "unknown"),
        "source": fields.get("source", "unknown"),
        "entity_key": fields.get("entity_key", "unknown"),
        "entity": entity,
        "incident_type_hint": fields.get("incident_type_hint", "incident.unknown"),
        "severity": fields.get("severity", "medium"),
        "payload": payload,
        "timestamp": fields.get("timestamp", ""),
    }


# ---------------------------------------------------------------------------
# CorrelationWorker
# ---------------------------------------------------------------------------


class CorrelationWorker:
    """Correlates signals into incidents and starts workflows."""

    def __init__(
        self,
        settings: Settings,
        redis: Redis,
        session_factory: async_sessionmaker[AsyncSession],
        temporal: TemporalClientProtocol | None = None,
    ) -> None:
        self.settings = settings
        self.redis = redis
        self.session_factory = session_factory
        self.temporal = temporal
        self.consumer_name = settings.worker_id
        self.task_queue = settings.temporal_task_queue
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self._running = True
        await rs.ensure_consumer_group(
            self.redis, rs.STREAM_CLASSIFIED_EVENTS, rs.GROUP_CORRELATION_WORKERS,
        )
        await rs.ensure_consumer_group(
            self.redis, rs.STREAM_CORRELATION_INPUT, rs.GROUP_CORRELATION_WORKERS,
        )
        logger.info("CorrelationWorker starting (consumer=%s)", self.consumer_name)
        await self._main_loop()

    async def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _main_loop(self) -> None:
        while self._running:
            # Read from both streams
            for stream, group, normalizer in [
                (rs.STREAM_CLASSIFIED_EVENTS, rs.GROUP_CORRELATION_WORKERS, _normalize_classified_event),
                (rs.STREAM_CORRELATION_INPUT, rs.GROUP_CORRELATION_WORKERS, _normalize_correlation_input),
            ]:
                entries = await rs.stream_read_group(
                    self.redis, stream, group, self.consumer_name,
                    count=50, block=500,
                )
                for entry_id, fields in entries:
                    try:
                        signal = normalizer(fields)
                        await self._process_signal(signal)
                    except Exception:
                        logger.exception("Error processing signal %s from %s", entry_id, stream)
                    finally:
                        await rs.stream_ack(self.redis, stream, group, entry_id)

    # ------------------------------------------------------------------
    # Core decision logic (§11.5)
    # ------------------------------------------------------------------

    async def process_signal(self, signal: dict[str, Any]) -> dict[str, Any]:
        """Public entry point for processing a single signal.

        Returns a result dict describing what happened. Useful for testing.
        """
        return await self._process_signal(signal)

    async def _process_signal(self, signal: dict[str, Any]) -> dict[str, Any]:
        entity_key = signal["entity_key"]
        incident_type = signal["incident_type_hint"]
        incident_key = build_incident_key(entity_key, incident_type)

        # Acquire lock
        acquired = await rs.acquire_lock(
            self.redis, "correlation", incident_key,
            self.consumer_name, ttl=10,
        )
        if not acquired:
            return {"action": "lock_contention", "incident_key": incident_key}

        try:
            # Check active incident
            active = await rs.get_active_incident(self.redis, incident_key)

            if active is None:
                return await self._handle_new_incident(signal, incident_key, incident_type)
            else:
                return await self._handle_existing_incident(signal, incident_key, active)
        finally:
            await rs.release_lock(
                self.redis, "correlation", incident_key, self.consumer_name,
            )

    # ------------------------------------------------------------------
    # New incident path
    # ------------------------------------------------------------------

    async def _handle_new_incident(
        self,
        signal: dict[str, Any],
        incident_key: str,
        incident_type: str,
    ) -> dict[str, Any]:
        # Check cooldown
        if await rs.has_cooldown(self.redis, incident_key):
            logger.debug("Cooldown active for %s, suppressing", incident_key)
            return {"action": "suppressed_cooldown", "incident_key": incident_key}

        # Check dedup
        if await rs.has_incident_dedup(self.redis, incident_key):
            logger.debug("Dedup hit for %s, skipping", incident_key)
            return {"action": "suppressed_dedup", "incident_key": incident_key}

        # Create incident in Postgres
        async with self.session_factory() as session:
            incident = await pg.create_incident(
                session,
                incident_key=incident_key,
                incident_type=incident_type,
                severity=signal.get("severity", "medium"),
                entity=signal.get("entity", {}),
                sources=[signal.get("source", "unknown")],
                evidence={"raw_signals": [signal.get("payload", {})]},
            )
            await pg.add_event(
                session, incident.id, "created",
                {"signal_id": signal.get("signal_id"), "source": signal.get("source")},
                actor="correlation-worker",
            )

            # Set Redis caches
            await rs.set_active_incident(
                self.redis, incident_key, str(incident.id), status="open",
            )
            await rs.set_incident_dedup(
                self.redis, incident_key, str(incident.id),
                ttl=self.settings.incident_cooldown_seconds * 2,
            )

            # Broadcast event to WebSocket subscribers
            from app.services.broadcast import publish_incident_event
            await publish_incident_event(
                self.redis, "incident_created",
                incident_id=str(incident.id),
                incident_key=incident_key,
                extra={
                    "incident_type": incident_type,
                    "severity": signal.get("severity", "medium"),
                    "entity": signal.get("entity", {}),
                },
            )

            # Look up matching playbook
            rule = await pg.find_playbook_for_incident(session, incident_type)

            if rule is not None and self.temporal is not None:
                # Task 11.8c — kill switch check. If the incident's namespace
                # is owned by a project with playbooks_enabled=false, record
                # the incident but suppress remediation. Missing project or
                # missing namespace → proceed (don't gate on something we
                # can't resolve).
                namespace = (signal.get("entity") or {}).get("namespace")
                if namespace:
                    project = await pg.get_project_by_namespace(session, namespace)
                    if project is not None and not project.playbooks_enabled:
                        await pg.add_event(
                            session, incident.id, "playbooks_disabled_for_namespace",
                            {"namespace": namespace, "project_id": str(project.id)},
                            actor="correlation-worker",
                        )
                        await session.commit()
                        logger.info(
                            "Playbooks disabled for namespace %s (project %s); "
                            "incident %s created, workflow suppressed",
                            namespace, project.id, incident.id,
                        )
                        return {
                            "action": "incident_created_playbooks_disabled",
                            "incident_id": str(incident.id),
                            "incident_key": incident_key,
                            "namespace": namespace,
                            "project_id": str(project.id),
                        }

                result = await self._start_workflow(session, incident, rule, signal)
                await session.commit()
                return {
                    "action": "incident_created_workflow_started",
                    "incident_id": str(incident.id),
                    "incident_key": incident_key,
                    **result,
                }

            if rule is None:
                await pg.add_event(
                    session, incident.id, "no_playbook_matched",
                    {"incident_type": incident_type},
                    actor="correlation-worker",
                )

            await session.commit()
            return {
                "action": "incident_created",
                "incident_id": str(incident.id),
                "incident_key": incident_key,
                "playbook_matched": rule is not None,
            }

    # ------------------------------------------------------------------
    # Existing incident path
    # ------------------------------------------------------------------

    async def _handle_existing_incident(
        self,
        signal: dict[str, Any],
        incident_key: str,
        active: dict[str, str],
    ) -> dict[str, Any]:
        incident_id_str = active["incident_id"]

        async with self.session_factory() as session:
            from uuid import UUID
            incident_id = UUID(incident_id_str)

            # Add signal as evidence
            await pg.add_event(
                session, incident_id, "signal_added",
                {
                    "signal_id": signal.get("signal_id"),
                    "source": signal.get("source"),
                    "payload": signal.get("payload", {}),
                },
                actor="correlation-worker",
            )

            # Severity escalation (§11.6)
            severity_order = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
            signal_severity = signal.get("severity", "medium")
            incident = await pg.get_incident(session, incident_id)
            if incident is not None:
                current_level = severity_order.get(incident.severity, 2)
                signal_level = severity_order.get(signal_severity, 2)
                if signal_level > current_level:
                    await pg.update_incident(
                        session, incident_id, severity=signal_severity,
                    )
                    logger.info(
                        "Escalated %s severity %s → %s",
                        incident_key, incident.severity, signal_severity,
                    )

            await session.commit()

        # Signal running Temporal workflow if present
        workflow_id = active.get("workflow_id")
        if workflow_id and self.temporal is not None:
            try:
                handle = self.temporal.get_workflow_handle(workflow_id)
                await handle.signal("new_evidence", signal)
                logger.info("Signaled workflow %s with new evidence", workflow_id)
            except Exception:
                logger.warning("Failed to signal workflow %s", workflow_id, exc_info=True)

        return {
            "action": "signal_added",
            "incident_id": incident_id_str,
            "incident_key": incident_key,
        }

    # ------------------------------------------------------------------
    # Temporal workflow start
    # ------------------------------------------------------------------

    async def _start_workflow(
        self,
        session: AsyncSession,
        incident: Any,
        rule: Any,
        signal: dict[str, Any],
    ) -> dict[str, Any]:
        short_id = uuid4().hex[:8]
        workflow_id = f"automend-{signal['entity_key']}-{short_id}"
        assert self.temporal is not None, "_start_workflow requires a Temporal client"

        try:
            handle = await self.temporal.start_workflow(
                "DynamicPlaybookExecutor",
                {
                    "playbook_version_id": str(rule.playbook_version_id),
                    "incident_id": str(incident.id),
                    "incident_payload": {
                        "incident_key": incident.incident_key,
                        "incident_type": incident.incident_type,
                        "entity": signal.get("entity", {}),
                        "severity": signal.get("severity", "medium"),
                        "sources": [signal.get("source", "unknown")],
                    },
                },
                id=workflow_id,
                task_queue=self.task_queue,
            )
            run_id = getattr(handle, "run_id", None) or ""

            await pg.update_incident(
                session, incident.id,
                temporal_workflow_id=workflow_id,
                temporal_run_id=run_id,
            )
            await pg.add_event(
                session, incident.id, "workflow_started",
                {"workflow_id": workflow_id, "run_id": run_id},
                actor="correlation-worker",
            )
            # Update Redis cache with workflow_id
            await rs.set_active_incident(
                self.redis, incident.incident_key,
                str(incident.id), status="open", workflow_id=workflow_id,
            )

            # Broadcast workflow start to WebSocket subscribers
            from app.services.broadcast import publish_workflow_event
            await publish_workflow_event(
                self.redis, "workflow_started",
                workflow_id=workflow_id, incident_id=str(incident.id),
                extra={"run_id": run_id, "task_queue": self.task_queue},
            )

            return {"workflow_id": workflow_id, "run_id": run_id}

        except Exception:
            logger.exception("Failed to start workflow for %s", incident.incident_key)
            await pg.add_event(
                session, incident.id, "workflow_start_failed",
                {"error": "temporal_unavailable"},
                actor="correlation-worker",
            )
            return {"workflow_id": None, "run_id": None}
