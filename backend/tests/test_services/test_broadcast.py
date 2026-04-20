"""Tests for the event broadcast service and WebSocket endpoint.

Broadcast tests: unit tests with real Redis on port 6380.
WebSocket tests: use FastAPI TestClient websocket_connect.
"""

from __future__ import annotations

import asyncio
import json
import socket
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio

try:
    import jwt
    from redis.asyncio import Redis

    from app.config import get_settings
    from app.services.broadcast import (
        CHANNEL_ALL,
        CHANNEL_INCIDENTS,
        CHANNEL_WORKFLOWS,
        publish_event,
        publish_incident_event,
        publish_workflow_event,
    )

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False

REDIS_PORT = 6380


def _redis_available() -> bool:
    if not _HAS_DEPS:
        return False
    try:
        s = socket.create_connection(("localhost", REDIS_PORT), timeout=2)
        s.close()
        return True
    except Exception:
        return False


_redis_is_up = _redis_available()
pytestmark = pytest.mark.skipif(not _redis_is_up, reason="Redis not available on port 6380")


@pytest_asyncio.fixture()
async def r():
    client = Redis.from_url(f"redis://localhost:{REDIS_PORT}/7", decode_responses=True)
    yield client
    await client.aclose()


# ===================================================================
# BROADCAST UNIT TESTS
# ===================================================================


class TestPublishEvent:
    async def test_publish_to_default_channels(self, r):
        # Subscribe before publishing
        pubsub = r.pubsub()
        await pubsub.subscribe(CHANNEL_INCIDENTS)

        await publish_event(r, "test_event", {"foo": "bar"})

        # Drain messages
        await asyncio.sleep(0.05)
        received = None
        for _ in range(10):
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if msg and msg["type"] == "message":
                received = json.loads(msg["data"])
                break

        assert received is not None
        assert received["event_type"] == "test_event"
        assert received["data"] == {"foo": "bar"}

        await pubsub.unsubscribe()
        await pubsub.aclose()

    async def test_publish_to_specific_channel(self, r):
        custom = "automend:events:custom"
        pubsub = r.pubsub()
        await pubsub.subscribe(custom)

        count = await publish_event(r, "custom_event", {"x": 1}, channels=[custom])

        await asyncio.sleep(0.05)
        msg = None
        for _ in range(10):
            m = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if m and m["type"] == "message":
                msg = m
                break

        assert msg is not None
        assert count >= 1

        await pubsub.aclose()


class TestPublishIncidentEvent:
    async def test_includes_incident_fields(self, r):
        pubsub = r.pubsub()
        await pubsub.subscribe(CHANNEL_INCIDENTS)

        inc_id = str(uuid4())
        await publish_incident_event(
            r, "incident_created",
            incident_id=inc_id, incident_key="prod/ml/trainer/mem",
            extra={"severity": "high"},
        )

        await asyncio.sleep(0.05)
        received = None
        for _ in range(10):
            m = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if m and m["type"] == "message":
                received = json.loads(m["data"])
                break

        assert received is not None
        assert received["event_type"] == "incident_created"
        assert received["data"]["incident_id"] == inc_id
        assert received["data"]["incident_key"] == "prod/ml/trainer/mem"
        assert received["data"]["severity"] == "high"

        await pubsub.aclose()


class TestPublishWorkflowEvent:
    async def test_workflow_event_on_workflow_channel(self, r):
        pubsub = r.pubsub()
        await pubsub.subscribe(CHANNEL_WORKFLOWS)

        await publish_workflow_event(
            r, "workflow_started",
            workflow_id="wf-abc", incident_id="inc-123",
            extra={"run_id": "run-1"},
        )

        await asyncio.sleep(0.05)
        received = None
        for _ in range(10):
            m = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if m and m["type"] == "message":
                received = json.loads(m["data"])
                break

        assert received is not None
        assert received["data"]["workflow_id"] == "wf-abc"
        assert received["data"]["run_id"] == "run-1"

        await pubsub.aclose()


# ===================================================================
# WEBSOCKET ENDPOINT TESTS
# ===================================================================


try:
    import psycopg2
    from fastapi.testclient import TestClient

    from main_api import create_app

    _HAS_WS_DEPS = True
except ImportError:
    _HAS_WS_DEPS = False


def _pg_available() -> bool:
    if not _HAS_WS_DEPS:
        return False
    try:
        conn = psycopg2.connect(
            dbname="automend", user="automend", password="automend",
            host="localhost", port=5432, connect_timeout=3,
        )
        conn.close()
        return True
    except Exception:
        return False


def _make_token(payload: dict | None = None) -> str:
    settings = get_settings()
    data = {
        "sub": "user@test.com",
        "role": "viewer",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=60),
    }
    if payload:
        data.update(payload)
    return jwt.encode(data, settings.jwt_secret, algorithm="HS256")


@pytest.fixture()
def ws_app(monkeypatch):
    """Build an app with Redis pointed at the test port 6380."""
    # Override the Redis URL in Settings
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("AUTOMEND_REDIS_PORT", str(REDIS_PORT))
    yield create_app()
    get_settings.cache_clear()


@pytest.mark.skipif(not _pg_available(), reason="Postgres needed for app lifespan")
class TestWebSocketEndpoint:
    def test_ws_rejects_invalid_token(self, ws_app):
        with TestClient(ws_app) as client:
            with pytest.raises(Exception):
                with client.websocket_connect("/api/ws/incidents?token=invalid"):
                    pass

    def test_ws_connects_with_valid_token(self, ws_app):
        """Connect, receive ready message, then disconnect."""
        with TestClient(ws_app) as client:
            token = _make_token()
            with client.websocket_connect(f"/api/ws/incidents?token={token}") as ws:
                msg = ws.receive_json()
                assert msg["event_type"] == "ready"
                assert msg["channel"] == "all"

    def test_ws_receives_published_event(self, ws_app):
        """Publish an event via broadcast, receive it over WS."""
        import redis as sync_redis
        with TestClient(ws_app) as client:
            token = _make_token()
            with client.websocket_connect(
                f"/api/ws/incidents?token={token}&channel=incidents"
            ) as ws:
                # Skip the ready message
                ws.receive_json()

                # Publish an event on the test Redis (port 6380)
                r = sync_redis.Redis(host="localhost", port=REDIS_PORT, decode_responses=True)
                payload = json.dumps({
                    "event_type": "incident_created",
                    "data": {"incident_id": "inc-abc", "incident_key": "test/key"},
                })
                import time
                time.sleep(0.2)
                r.publish(CHANNEL_INCIDENTS, payload)
                r.close()

                # Receive (may get heartbeat first)
                received_event = None
                for _ in range(5):
                    msg = ws.receive_json()
                    if msg.get("event_type") == "incident_created":
                        received_event = msg
                        break

                assert received_event is not None
                assert received_event["data"]["incident_id"] == "inc-abc"
