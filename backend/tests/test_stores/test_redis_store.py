"""Tests for app.stores.redis_store — windows, dedup, locks, streams, caching.

Requires a running Redis instance on localhost:6379.
Tests are skipped automatically if Redis is not reachable.

To run:  docker compose -f infra/docker-compose.infra.yml up -d redis
         cd backend && conda run -n mlops_project pytest tests/test_stores/test_redis_store.py -v
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
import pytest_asyncio

try:
    from redis.asyncio import Redis
    from app.stores import redis_store as rs
    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False


REDIS_TEST_PORT = 6380  # Use 6380 to avoid conflicting with Ray/other services on 6379


def _redis_available() -> bool:
    if not _HAS_DEPS:
        return False
    import socket
    try:
        s = socket.create_connection(("localhost", REDIS_TEST_PORT), timeout=2)
        s.close()
        return True
    except Exception:
        return False


_redis_is_up = _redis_available()
pytestmark = pytest.mark.skipif(not _redis_is_up, reason="Redis not available")


@pytest_asyncio.fixture()
async def r():
    """Provide a Redis client. Clean up all test keys after each test."""
    client = Redis.from_url(f"redis://localhost:{REDIS_TEST_PORT}/1", decode_responses=True)
    yield client
    # Cleanup: delete all keys with our test prefix
    async for key in client.scan_iter(match="automend:*", count=500):
        await client.delete(key)
    await client.aclose()


# ===================================================================
# WINDOWS
# ===================================================================


class TestWindows:
    async def test_add_log_creates_window(self, r):
        ek = f"test/{uuid4().hex[:8]}"
        meta = await rs.add_log_to_window(r, ek, '{"body": "test log"}')
        assert "window_start" in meta
        assert int(meta["count"]) == 1

    async def test_add_multiple_logs_increments_count(self, r):
        ek = f"test/{uuid4().hex[:8]}"
        await rs.add_log_to_window(r, ek, '{"body": "log1"}')
        meta = await rs.add_log_to_window(r, ek, '{"body": "log2"}')
        assert int(meta["count"]) == 2

    async def test_get_window_entries(self, r):
        ek = f"test/{uuid4().hex[:8]}"
        await rs.add_log_to_window(r, ek, '{"body": "a"}')
        await rs.add_log_to_window(r, ek, '{"body": "b"}')
        entries = await rs.get_window_entries(r, ek)
        assert len(entries) == 2
        assert json.loads(entries[0])["body"] == "a"

    async def test_get_window_meta(self, r):
        ek = f"test/{uuid4().hex[:8]}"
        await rs.add_log_to_window(r, ek, '{"body": "x"}')
        meta = await rs.get_window_meta(r, ek)
        assert "window_start" in meta
        assert "last_seen" in meta

    async def test_close_window_deletes_data(self, r):
        ek = f"test/{uuid4().hex[:8]}"
        await rs.add_log_to_window(r, ek, '{"body": "x"}')
        await rs.close_window(r, ek)
        entries = await rs.get_window_entries(r, ek)
        assert entries == []
        meta = await rs.get_window_meta(r, ek)
        assert meta == {}

    async def test_scan_open_windows(self, r):
        ek = f"test/{uuid4().hex[:8]}"
        await rs.add_log_to_window(r, ek, '{"body": "x"}')
        open_keys = await rs.scan_open_windows(r)
        assert ek in open_keys


# ===================================================================
# DEDUP
# ===================================================================


class TestDedup:
    async def test_classifier_dedup(self, r):
        ek = f"test/{uuid4().hex[:8]}"
        assert await rs.has_classifier_dedup(r, ek, "failure.memory") is False
        await rs.set_classifier_dedup(r, ek, "failure.memory", ttl=10)
        assert await rs.has_classifier_dedup(r, ek, "failure.memory") is True

    async def test_incident_dedup(self, r):
        ik = f"test/{uuid4().hex[:8]}/failure.memory"
        assert await rs.has_incident_dedup(r, ik) is False
        await rs.set_incident_dedup(r, ik, "inc-123", ttl=10)
        assert await rs.has_incident_dedup(r, ik) is True


# ===================================================================
# COOLDOWN
# ===================================================================


class TestCooldown:
    async def test_cooldown(self, r):
        ik = f"test/{uuid4().hex[:8]}/failure.gpu"
        assert await rs.has_cooldown(r, ik) is False
        await rs.set_cooldown(r, ik, ttl=10)
        assert await rs.has_cooldown(r, ik) is True


# ===================================================================
# ACTIVE INCIDENT CACHE
# ===================================================================


class TestActiveIncident:
    async def test_set_and_get(self, r):
        ik = f"test/{uuid4().hex[:8]}"
        await rs.set_active_incident(r, ik, "inc-1", status="open", workflow_id="wf-1")
        data = await rs.get_active_incident(r, ik)
        assert data is not None
        assert data["incident_id"] == "inc-1"
        assert data["status"] == "open"
        assert data["workflow_id"] == "wf-1"

    async def test_get_nonexistent_returns_none(self, r):
        data = await rs.get_active_incident(r, "nonexistent")
        assert data is None

    async def test_delete(self, r):
        ik = f"test/{uuid4().hex[:8]}"
        await rs.set_active_incident(r, ik, "inc-1")
        await rs.delete_active_incident(r, ik)
        assert await rs.get_active_incident(r, ik) is None


# ===================================================================
# DISTRIBUTED LOCKS
# ===================================================================


class TestLocks:
    async def test_acquire_and_release(self, r):
        key = f"test/{uuid4().hex[:8]}"
        assert await rs.acquire_lock(r, "window", key, "w-0", ttl=10) is True
        # Same worker can't acquire again
        assert await rs.acquire_lock(r, "window", key, "w-0", ttl=10) is False
        # Different worker can't acquire
        assert await rs.acquire_lock(r, "window", key, "w-1", ttl=10) is False
        # Owner can release
        assert await rs.release_lock(r, "window", key, "w-0") is True

    async def test_release_by_wrong_worker_fails(self, r):
        key = f"test/{uuid4().hex[:8]}"
        await rs.acquire_lock(r, "correlation", key, "w-0", ttl=10)
        assert await rs.release_lock(r, "correlation", key, "w-1") is False

    async def test_release_nonexistent_returns_false(self, r):
        assert await rs.release_lock(r, "window", "nope", "w-0") is False


# ===================================================================
# LAST SEEN
# ===================================================================


class TestLastSeen:
    async def test_set_and_get(self, r):
        ek = f"test/{uuid4().hex[:8]}"
        await rs.set_last_seen(r, ek, ttl=10)
        val = await rs.get_last_seen(r, ek)
        assert val is not None  # ISO timestamp string

    async def test_get_nonexistent_returns_none(self, r):
        assert await rs.get_last_seen(r, "nonexistent") is None


# ===================================================================
# STREAMS
# ===================================================================


class TestStreams:
    async def test_ensure_consumer_group_idempotent(self, r):
        stream = f"automend:test_stream_{uuid4().hex[:8]}"
        await rs.ensure_consumer_group(r, stream, "test-group")
        # Second call should not raise
        await rs.ensure_consumer_group(r, stream, "test-group")

    async def test_stream_add_and_len(self, r):
        stream = f"automend:test_stream_{uuid4().hex[:8]}"
        eid = await rs.stream_add(r, stream, {"key": "value"})
        assert eid is not None
        length = await rs.stream_len(r, stream)
        assert length == 1

    async def test_stream_add_with_maxlen(self, r):
        stream = f"automend:test_stream_{uuid4().hex[:8]}"
        for i in range(200):
            await rs.stream_add(r, stream, {"i": str(i)}, maxlen=100)
        length = await rs.stream_len(r, stream)
        # Approximate trimming: Redis may keep slightly more than maxlen
        assert length <= 150

    async def test_read_group_and_ack(self, r):
        stream = f"automend:test_stream_{uuid4().hex[:8]}"
        group = "test-group"
        consumer = "c-0"

        await rs.ensure_consumer_group(r, stream, group)
        await rs.stream_add(r, stream, {"body": "hello"})
        await rs.stream_add(r, stream, {"body": "world"})

        entries = await rs.stream_read_group(
            r, stream, group, consumer, count=10, block=100
        )
        assert len(entries) == 2
        msg_id_0, fields_0 = entries[0]
        assert fields_0["body"] == "hello"

        # Ack the entries
        ids = [e[0] for e in entries]
        acked = await rs.stream_ack(r, stream, group, *ids)
        assert acked == 2

    async def test_read_group_empty_returns_empty(self, r):
        stream = f"automend:test_stream_{uuid4().hex[:8]}"
        group = "test-group"
        await rs.ensure_consumer_group(r, stream, group)
        entries = await rs.stream_read_group(
            r, stream, group, "c-0", count=10, block=100
        )
        assert entries == []

    async def test_stream_ack_empty_returns_zero(self, r):
        result = await rs.stream_ack(r, "automend:nope", "g", )
        assert result == 0


# ===================================================================
# KEY CONSTANTS
# ===================================================================


class TestKeyConstants:
    def test_stream_names(self):
        assert rs.STREAM_NORMALIZED_LOGS == "automend:stream:normalized_logs"
        assert rs.STREAM_CLASSIFIED_EVENTS == "automend:stream:classified_events"
        assert rs.STREAM_CORRELATION_INPUT == "automend:stream:correlation_input"

    def test_group_names(self):
        assert rs.GROUP_WINDOW_WORKERS == "window-workers"
        assert rs.GROUP_CORRELATION_WORKERS == "correlation-workers"
