"""Tests for the WindowWorker (§9.3).

Integration tests use Redis on port 6380. Skip if not available.
Uses a mock classifier to avoid needing the classifier service.
"""

from __future__ import annotations

import json
import socket
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio

try:
    from redis.asyncio import Redis

    from app.config import Settings
    from app.stores import redis_store as rs
    from app.workers.window_worker import WindowWorker

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False

REDIS_TEST_PORT = 6380


def _redis_available() -> bool:
    if not _HAS_DEPS:
        return False
    try:
        s = socket.create_connection(("localhost", REDIS_TEST_PORT), timeout=2)
        s.close()
        return True
    except Exception:
        return False


_redis_is_up = _redis_available()
pytestmark = pytest.mark.skipif(not _redis_is_up, reason="Redis not available on port 6380")


# ---------------------------------------------------------------------------
# Mock classifier
# ---------------------------------------------------------------------------


class MockClassifier:
    """Configurable mock classifier for testing."""

    def __init__(
        self,
        label: str = "failure.memory",
        confidence: float = 0.94,
        evidence: list[str] | None = None,
        severity_suggestion: str = "high",
        should_raise: bool = False,
    ):
        self.label = label
        self.confidence = confidence
        self.evidence = evidence or ["CUDA error: out of memory"]
        self.severity_suggestion = severity_suggestion
        self.should_raise = should_raise
        self.call_count = 0
        self.last_input: dict | None = None

    async def classify(self, input_data: dict) -> dict:
        self.call_count += 1
        self.last_input = input_data
        if self.should_raise:
            raise RuntimeError("Classifier unavailable")
        return {
            "label": self.label,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "severity_suggestion": self.severity_suggestion,
            "secondary_labels": [],
        }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _test_settings(**overrides) -> Settings:
    defaults = dict(
        worker_id="test-worker-0",
        window_size_seconds=300,
        max_window_entries=500,
        window_check_interval_seconds=1,
        classifier_confidence_threshold=0.7,
        dedup_cooldown_seconds=900,
        redis_host="localhost",
        redis_port=REDIS_TEST_PORT,
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest_asyncio.fixture()
async def redis_client():
    r = Redis.from_url(f"redis://localhost:{REDIS_TEST_PORT}/2", decode_responses=True)
    yield r
    async for key in r.scan_iter(match="automend:*", count=500):
        await r.delete(key)
    await r.aclose()


def _unique_entity() -> str:
    return f"test/{uuid4().hex[:8]}"


def _log_entry(body: str = "test log", entity_key: str = "test/entity") -> dict[str, str]:
    return {
        "entity_key": entity_key,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "body": body,
        "severity": "ERROR",
        "attributes": json.dumps({"cluster": "test", "namespace": "ml", "service": "svc"}),
    }


# ===================================================================
# WINDOW MANAGEMENT
# ===================================================================


class TestWindowManagement:
    async def test_process_entry_adds_to_window(self, redis_client):
        ek = _unique_entity()
        settings = _test_settings()
        classifier = MockClassifier()
        worker = WindowWorker(settings, redis_client, classifier)

        entry = _log_entry(entity_key=ek)
        await worker._process_entry("1-0", entry)

        entries = await rs.get_window_entries(redis_client, ek)
        assert len(entries) == 1
        meta = await rs.get_window_meta(redis_client, ek)
        assert int(meta["count"]) == 1

    async def test_multiple_entries_accumulate(self, redis_client):
        ek = _unique_entity()
        settings = _test_settings()
        worker = WindowWorker(settings, redis_client, MockClassifier())

        for i in range(5):
            await worker._process_entry(f"{i}-0", _log_entry(f"log {i}", ek))

        entries = await rs.get_window_entries(redis_client, ek)
        assert len(entries) == 5

    async def test_should_close_by_count(self, redis_client):
        settings = _test_settings(max_window_entries=3)
        worker = WindowWorker(settings, redis_client, MockClassifier())

        meta = {"count": "3", "window_start": datetime.now(timezone.utc).isoformat()}
        assert worker._should_close_window(meta) is True

    async def test_should_close_by_time(self, redis_client):
        settings = _test_settings(window_size_seconds=300)
        worker = WindowWorker(settings, redis_client, MockClassifier())

        old_start = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
        meta = {"count": "10", "window_start": old_start}
        assert worker._should_close_window(meta) is True

    async def test_should_not_close_fresh_window(self, redis_client):
        settings = _test_settings()
        worker = WindowWorker(settings, redis_client, MockClassifier())

        meta = {"count": "5", "window_start": datetime.now(timezone.utc).isoformat()}
        assert worker._should_close_window(meta) is False


# ===================================================================
# CLOSE WINDOW + CLASSIFICATION
# ===================================================================


class TestCloseWindow:
    async def test_close_window_calls_classifier(self, redis_client):
        ek = _unique_entity()
        classifier = MockClassifier()
        settings = _test_settings()
        worker = WindowWorker(settings, redis_client, classifier)

        # Add some logs
        for i in range(3):
            await rs.add_log_to_window(redis_client, ek, json.dumps(_log_entry(f"log {i}", ek)))

        event = await worker.close_window(ek)

        assert classifier.call_count == 1
        assert classifier.last_input["entity_key"] == ek
        assert len(classifier.last_input["logs"]) == 3
        assert event is not None
        assert event["classification"]["label"] == "failure.memory"

    async def test_close_window_emits_to_stream(self, redis_client):
        ek = _unique_entity()
        classifier = MockClassifier()
        worker = WindowWorker(_test_settings(), redis_client, classifier)

        await rs.add_log_to_window(redis_client, ek, json.dumps(_log_entry(entity_key=ek)))
        await worker.close_window(ek)

        length = await rs.stream_len(redis_client, rs.STREAM_CLASSIFIED_EVENTS)
        assert length >= 1

    async def test_close_window_sets_dedup(self, redis_client):
        ek = _unique_entity()
        classifier = MockClassifier(label="failure.gpu")
        worker = WindowWorker(_test_settings(), redis_client, classifier)

        await rs.add_log_to_window(redis_client, ek, json.dumps(_log_entry(entity_key=ek)))
        await worker.close_window(ek)

        assert await rs.has_classifier_dedup(redis_client, ek, "failure.gpu") is True

    async def test_close_window_cleans_up(self, redis_client):
        ek = _unique_entity()
        worker = WindowWorker(_test_settings(), redis_client, MockClassifier())

        await rs.add_log_to_window(redis_client, ek, json.dumps(_log_entry(entity_key=ek)))
        await worker.close_window(ek)

        assert await rs.get_window_entries(redis_client, ek) == []
        assert await rs.get_window_meta(redis_client, ek) == {}

    async def test_close_window_skips_below_threshold(self, redis_client):
        ek = _unique_entity()
        classifier = MockClassifier(confidence=0.3)
        worker = WindowWorker(_test_settings(), redis_client, classifier)

        await rs.add_log_to_window(redis_client, ek, json.dumps(_log_entry(entity_key=ek)))
        event = await worker.close_window(ek)

        assert event is None  # Below threshold, no event emitted

    async def test_close_window_skips_dedup(self, redis_client):
        ek = _unique_entity()
        classifier = MockClassifier(label="failure.memory")
        worker = WindowWorker(_test_settings(), redis_client, classifier)

        # Pre-set dedup
        await rs.set_classifier_dedup(redis_client, ek, "failure.memory", ttl=60)

        await rs.add_log_to_window(redis_client, ek, json.dumps(_log_entry(entity_key=ek)))
        event = await worker.close_window(ek)

        assert event is None

    async def test_close_window_handles_classifier_error(self, redis_client):
        ek = _unique_entity()
        classifier = MockClassifier(should_raise=True)
        worker = WindowWorker(_test_settings(), redis_client, classifier)

        await rs.add_log_to_window(redis_client, ek, json.dumps(_log_entry(entity_key=ek)))
        event = await worker.close_window(ek)

        assert event is None
        # Window should still be cleaned up
        assert await rs.get_window_entries(redis_client, ek) == []

    async def test_close_window_no_classifier(self, redis_client):
        ek = _unique_entity()
        worker = WindowWorker(_test_settings(), redis_client, classifier=None)

        await rs.add_log_to_window(redis_client, ek, json.dumps(_log_entry(entity_key=ek)))
        event = await worker.close_window(ek)

        assert event is None

    async def test_close_empty_window(self, redis_client):
        ek = _unique_entity()
        worker = WindowWorker(_test_settings(), redis_client, MockClassifier())
        event = await worker.close_window(ek)
        assert event is None


# ===================================================================
# CLASSIFIED EVENT STRUCTURE
# ===================================================================


class TestClassifiedEvent:
    async def test_event_structure(self, redis_client):
        ek = _unique_entity()
        classifier = MockClassifier(
            label="failure.memory",
            confidence=0.94,
            evidence=["CUDA OOM"],
            severity_suggestion="high",
        )
        worker = WindowWorker(_test_settings(), redis_client, classifier)

        await rs.add_log_to_window(redis_client, ek, json.dumps(_log_entry(entity_key=ek)))
        event = await worker.close_window(ek)

        assert event["event_type"] == "classified_log_event"
        assert event["entity_key"] == ek
        assert event["classification"]["label"] == "failure.memory"
        assert event["classification"]["confidence"] == 0.94
        assert event["classification"]["evidence"] == ["CUDA OOM"]
        assert event["classification"]["severity_suggestion"] == "high"
        assert event["window"]["log_count"] == 1
        assert "timestamp" in event


# ===================================================================
# AUTO-CLOSE BY MAX ENTRIES
# ===================================================================


class TestAutoClose:
    async def test_auto_close_on_max_entries(self, redis_client):
        ek = _unique_entity()
        settings = _test_settings(max_window_entries=3)
        classifier = MockClassifier()
        worker = WindowWorker(settings, redis_client, classifier)

        # Process entries — the 3rd should trigger close
        for i in range(3):
            await worker._process_entry(f"{i}-0", _log_entry(f"log {i}", ek))

        # After close, window should be cleaned up
        assert await rs.get_window_entries(redis_client, ek) == []
        assert classifier.call_count == 1
