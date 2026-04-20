"""Event broadcast service — publishes real-time events to Redis Pub/Sub.

WebSocket clients subscribe to these channels for live updates on
incidents, workflow executions, and step completions.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


# Channel names — WS endpoint subscribes to these
CHANNEL_INCIDENTS = "automend:events:incidents"
CHANNEL_WORKFLOWS = "automend:events:workflows"
CHANNEL_ALL = "automend:events:all"


async def publish_event(
    redis: Redis,
    event_type: str,
    data: dict[str, Any],
    channels: list[str] | None = None,
) -> int:
    """Publish an event to one or more Redis Pub/Sub channels.

    Returns the total number of subscribers that received the event.
    """
    payload = json.dumps({"event_type": event_type, "data": data})
    target_channels = channels or [CHANNEL_INCIDENTS, CHANNEL_ALL]

    total = 0
    for channel in target_channels:
        try:
            count = await redis.publish(channel, payload)
            total += count
        except Exception:
            logger.exception("Failed to publish to channel %s", channel)

    return total


async def publish_incident_event(
    redis: Redis,
    event_type: str,
    incident_id: str,
    incident_key: str,
    extra: dict[str, Any] | None = None,
) -> int:
    """Convenience helper for incident-scoped events."""
    data = {
        "incident_id": incident_id,
        "incident_key": incident_key,
        **(extra or {}),
    }
    return await publish_event(redis, event_type, data, channels=[CHANNEL_INCIDENTS, CHANNEL_ALL])


async def publish_workflow_event(
    redis: Redis,
    event_type: str,
    workflow_id: str,
    incident_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> int:
    """Convenience helper for workflow-scoped events."""
    data = {
        "workflow_id": workflow_id,
        "incident_id": incident_id,
        **(extra or {}),
    }
    return await publish_event(redis, event_type, data, channels=[CHANNEL_WORKFLOWS, CHANNEL_ALL])
