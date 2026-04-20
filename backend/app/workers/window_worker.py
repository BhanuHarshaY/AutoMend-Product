"""Window Worker — consumes normalized logs, maintains rolling windows,
calls the classifier, and emits classified events (§9.3).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from redis.asyncio import Redis

from app.config import Settings
from app.stores import redis_store as rs

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Classifier protocol — allows injection of real or mock classifier
# ---------------------------------------------------------------------------


class ClassifierProtocol(Protocol):
    async def classify(self, input_data: dict) -> dict:
        """Send a classification request, return the result dict."""
        ...


# ---------------------------------------------------------------------------
# WindowWorker
# ---------------------------------------------------------------------------


class WindowWorker:
    """Maintains rolling 5-minute log windows and classifies them."""

    def __init__(
        self,
        settings: Settings,
        redis: Redis,
        classifier: ClassifierProtocol | None = None,
    ) -> None:
        self.settings = settings
        self.redis = redis
        self.classifier = classifier
        self.consumer_name = settings.worker_id
        self.window_size = settings.window_size_seconds
        self.max_entries = settings.max_window_entries
        self.check_interval = settings.window_check_interval_seconds
        self.confidence_threshold = settings.classifier_confidence_threshold
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start the main loop and stale-window timer."""
        self._running = True
        await rs.ensure_consumer_group(
            self.redis, rs.STREAM_NORMALIZED_LOGS, rs.GROUP_WINDOW_WORKERS
        )
        logger.info(
            "WindowWorker starting (consumer=%s, window=%ds, max_entries=%d)",
            self.consumer_name, self.window_size, self.max_entries,
        )
        stale_task = asyncio.create_task(self._stale_window_timer())
        try:
            await self._main_loop()
        finally:
            self._running = False
            stale_task.cancel()
            try:
                await stale_task
            except asyncio.CancelledError:
                pass

    async def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _main_loop(self) -> None:
        while self._running:
            entries = await rs.stream_read_group(
                self.redis,
                rs.STREAM_NORMALIZED_LOGS,
                rs.GROUP_WINDOW_WORKERS,
                self.consumer_name,
                count=100,
                block=2000,
            )
            for entry_id, fields in entries:
                try:
                    await self._process_entry(entry_id, fields)
                except Exception:
                    logger.exception("Error processing log entry %s", entry_id)
                finally:
                    await rs.stream_ack(
                        self.redis, rs.STREAM_NORMALIZED_LOGS,
                        rs.GROUP_WINDOW_WORKERS, entry_id,
                    )

    # ------------------------------------------------------------------
    # Process a single log entry
    # ------------------------------------------------------------------

    async def _process_entry(self, entry_id: str, fields: dict[str, str]) -> None:
        entity_key = fields.get("entity_key", "unknown")
        log_json = json.dumps(fields)

        acquired = await rs.acquire_lock(
            self.redis, "window", entity_key, self.consumer_name, ttl=30
        )
        if not acquired:
            return  # Another worker is handling this entity

        try:
            meta = await rs.add_log_to_window(self.redis, entity_key, log_json)
            should_close = self._should_close_window(meta)
            if should_close:
                await self.close_window(entity_key)
        finally:
            await rs.release_lock(
                self.redis, "window", entity_key, self.consumer_name
            )

    def _should_close_window(self, meta: dict[str, Any]) -> bool:
        """Check if the window should close based on time or entry count."""
        count = int(meta.get("count", 0))
        if count >= self.max_entries:
            return True

        window_start_str = meta.get("window_start")
        if not window_start_str:
            return False

        try:
            window_start = datetime.fromisoformat(window_start_str)
            elapsed = (datetime.now(timezone.utc) - window_start).total_seconds()
            return elapsed >= self.window_size
        except (ValueError, TypeError):
            return False

    # ------------------------------------------------------------------
    # Close a window — classify and emit
    # ------------------------------------------------------------------

    async def close_window(self, entity_key: str) -> dict | None:
        """Close a window: retrieve logs, classify, emit event, cleanup.

        Returns the classified event dict if emitted, None otherwise.
        """
        entries = await rs.get_window_entries(self.redis, entity_key)
        meta = await rs.get_window_meta(self.redis, entity_key)

        if not entries:
            await rs.close_window(self.redis, entity_key)
            return None

        # Parse log entries
        logs = []
        for raw in entries:
            try:
                logs.append(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                logs.append({"body": str(raw)})

        window_start = meta.get("window_start", datetime.now(timezone.utc).isoformat())
        window_end = datetime.now(timezone.utc).isoformat()

        # Build entity context from first log's attributes
        entity_context: dict[str, str] = {}
        if logs:
            attrs_str = logs[0].get("attributes", "{}")
            if isinstance(attrs_str, str):
                try:
                    entity_context = json.loads(attrs_str)
                except json.JSONDecodeError:
                    pass
            elif isinstance(attrs_str, dict):
                entity_context = attrs_str

        # Call classifier
        if self.classifier is None:
            logger.warning("No classifier configured, skipping classification for %s", entity_key)
            await rs.close_window(self.redis, entity_key)
            return None

        classifier_input = {
            "entity_key": entity_key,
            "window_start": window_start,
            "window_end": window_end,
            "logs": logs[:200],  # max_logs
            "max_logs": 200,
            "entity_context": entity_context,
        }

        try:
            result = await self.classifier.classify(classifier_input)
        except Exception:
            logger.exception("Classifier call failed for %s", entity_key)
            await rs.close_window(self.redis, entity_key)
            return None

        label = result.get("label", "normal")
        confidence = float(result.get("confidence", 0.0))

        # Check confidence threshold
        if confidence < self.confidence_threshold:
            logger.debug(
                "Classification below threshold for %s: %s (%.2f < %.2f)",
                entity_key, label, confidence, self.confidence_threshold,
            )
            await rs.close_window(self.redis, entity_key)
            return None

        # Check dedup
        if await rs.has_classifier_dedup(self.redis, entity_key, label):
            logger.debug("Dedup hit for %s/%s, skipping", entity_key, label)
            await rs.close_window(self.redis, entity_key)
            return None

        # Build and emit classified event
        event = self._build_classified_event(
            entity_key=entity_key,
            entity_context=entity_context,
            label=label,
            confidence=confidence,
            evidence=result.get("evidence", []),
            severity_suggestion=result.get("severity_suggestion"),
            window_start=window_start,
            window_end=window_end,
            log_count=len(entries),
        )

        await rs.stream_add(
            self.redis,
            rs.STREAM_CLASSIFIED_EVENTS,
            _serialize_event(event),
            maxlen=10000,
        )

        # Set dedup key
        await rs.set_classifier_dedup(
            self.redis, entity_key, label,
            ttl=self.settings.dedup_cooldown_seconds,
        )

        logger.info(
            "Classified %s: %s (confidence=%.2f)", entity_key, label, confidence,
        )

        # Cleanup window
        await rs.close_window(self.redis, entity_key)
        return event

    # ------------------------------------------------------------------
    # Stale window timer
    # ------------------------------------------------------------------

    async def _stale_window_timer(self) -> None:
        """Background task that closes windows open longer than window_size."""
        while self._running:
            await asyncio.sleep(self.check_interval)
            try:
                open_keys = await rs.scan_open_windows(self.redis)
                for entity_key in open_keys:
                    meta = await rs.get_window_meta(self.redis, entity_key)
                    if self._should_close_window(meta):
                        acquired = await rs.acquire_lock(
                            self.redis, "window", entity_key,
                            self.consumer_name, ttl=30,
                        )
                        if acquired:
                            try:
                                logger.info("Closing stale window for %s", entity_key)
                                await self.close_window(entity_key)
                            finally:
                                await rs.release_lock(
                                    self.redis, "window", entity_key,
                                    self.consumer_name,
                                )
            except Exception:
                logger.exception("Error in stale window timer")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_classified_event(
        *,
        entity_key: str,
        entity_context: dict,
        label: str,
        confidence: float,
        evidence: list[str],
        severity_suggestion: str | None,
        window_start: str,
        window_end: str,
        log_count: int,
    ) -> dict:
        return {
            "event_id": str(uuid4()),
            "event_type": "classified_log_event",
            "entity_key": entity_key,
            "entity": entity_context,
            "classification": {
                "label": label,
                "confidence": confidence,
                "evidence": evidence,
                "severity_suggestion": severity_suggestion,
            },
            "window": {
                "start": window_start,
                "end": window_end,
                "log_count": log_count,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


def _serialize_event(event: dict) -> dict[str, str]:
    """Flatten a classified event dict to string values for Redis stream."""
    return {
        "event_id": event["event_id"],
        "event_type": event["event_type"],
        "entity_key": event["entity_key"],
        "entity": json.dumps(event["entity"]),
        "classification": json.dumps(event["classification"]),
        "window": json.dumps(event["window"]),
        "timestamp": event["timestamp"],
    }
