"""Redis store — window management, dedup, locks, streams, and caching.

Key design follows backend_architecture.md §6.  Every public function
receives a ``redis.asyncio.Redis`` client so the caller controls the
connection (matching the postgres_store pattern).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis

# ---------------------------------------------------------------------------
# Key builders
# ---------------------------------------------------------------------------

_PREFIX = "automend"


def _key(*parts: str) -> str:
    return f"{_PREFIX}:{':'.join(parts)}"


# --- public key constants for consumer groups / streams ---
STREAM_NORMALIZED_LOGS = _key("stream", "normalized_logs")
STREAM_CLASSIFIED_EVENTS = _key("stream", "classified_events")
STREAM_CORRELATION_INPUT = _key("stream", "correlation_input")

GROUP_WINDOW_WORKERS = "window-workers"
GROUP_CORRELATION_WORKERS = "correlation-workers"


# ===================================================================
# WINDOWS  (§9.3)
# ===================================================================


async def add_log_to_window(
    r: Redis,
    entity_key: str,
    log_json: str,
    *,
    window_ttl: int = 600,  # 10 min
) -> dict[str, Any]:
    """Append a log entry to the entity's rolling window.

    Returns the current window metadata (window_start, count, last_seen).
    """
    win_key = _key("window", entity_key)
    meta_key = _key("window", "meta", entity_key)
    now = datetime.now(timezone.utc).isoformat()

    pipe = r.pipeline(transaction=False)

    # Create metadata if this is the first entry
    pipe.hsetnx(meta_key, "window_start", now)
    pipe.rpush(win_key, log_json)
    pipe.hincrby(meta_key, "count", 1)
    pipe.hset(meta_key, "last_seen", now)
    # Refresh TTL on both keys
    pipe.expire(win_key, window_ttl)
    pipe.expire(meta_key, window_ttl)

    await pipe.execute()

    # Return current metadata
    meta = await r.hgetall(meta_key)
    return meta


async def get_window_entries(r: Redis, entity_key: str) -> list[str]:
    """Retrieve all log entries in the entity's current window."""
    return await r.lrange(_key("window", entity_key), 0, -1)


async def get_window_meta(r: Redis, entity_key: str) -> dict[str, str]:
    """Retrieve window metadata (window_start, count, last_seen)."""
    return await r.hgetall(_key("window", "meta", entity_key))


async def close_window(r: Redis, entity_key: str) -> None:
    """Delete the window data and metadata for an entity."""
    await r.delete(
        _key("window", entity_key),
        _key("window", "meta", entity_key),
    )


async def scan_open_windows(r: Redis) -> list[str]:
    """Return entity keys that have open window metadata.

    Used by the background timer to find stale windows.
    """
    pattern = _key("window", "meta", "*")
    prefix = _key("window", "meta") + ":"
    keys: list[str] = []
    async for key in r.scan_iter(match=pattern, count=200):
        # key looks like "automend:window:meta:prod-a/ml/trainer"
        entity_key = key.removeprefix(prefix) if isinstance(key, str) else key.decode().removeprefix(prefix)
        keys.append(entity_key)
    return keys


# ===================================================================
# DEDUP KEYS  (§6)
# ===================================================================


async def set_classifier_dedup(
    r: Redis, entity_key: str, label: str, ttl: int = 900
) -> None:
    """Set classifier dedup key (prevents re-classification for same label)."""
    await r.set(_key("dedup", "classifier", entity_key, label), "1", ex=ttl)


async def has_classifier_dedup(r: Redis, entity_key: str, label: str) -> bool:
    """Check if a classifier dedup key exists."""
    return bool(await r.exists(_key("dedup", "classifier", entity_key, label)))


async def set_incident_dedup(
    r: Redis, incident_key: str, incident_id: str, ttl: int = 1800
) -> None:
    """Set incident dedup key (prevents duplicate incident creation)."""
    await r.set(_key("dedup", "incident", incident_key), incident_id, ex=ttl)


async def has_incident_dedup(r: Redis, incident_key: str) -> bool:
    """Check if an incident dedup key exists."""
    return bool(await r.exists(_key("dedup", "incident", incident_key)))


# ===================================================================
# COOLDOWN  (§6)
# ===================================================================


async def set_cooldown(r: Redis, incident_key: str, ttl: int = 900) -> None:
    """Set cooldown key (suppresses duplicate workflow starts after resolution)."""
    await r.set(_key("cooldown", incident_key), "1", ex=ttl)


async def has_cooldown(r: Redis, incident_key: str) -> bool:
    """Check if an incident is in cooldown."""
    return bool(await r.exists(_key("cooldown", incident_key)))


# ===================================================================
# ACTIVE INCIDENT CACHE  (§6)
# ===================================================================


async def set_active_incident(
    r: Redis,
    incident_key: str,
    incident_id: str,
    status: str = "open",
    workflow_id: str = "",
) -> None:
    """Cache active incident state for fast lookup by correlation worker."""
    await r.hset(
        _key("incident", "active", incident_key),
        mapping={
            "incident_id": incident_id,
            "status": status,
            "workflow_id": workflow_id,
        },
    )


async def get_active_incident(r: Redis, incident_key: str) -> dict[str, str] | None:
    """Get cached active incident state, or None if no active incident."""
    data = await r.hgetall(_key("incident", "active", incident_key))
    return data if data else None


async def delete_active_incident(r: Redis, incident_key: str) -> None:
    """Remove active incident cache (e.g., after resolution)."""
    await r.delete(_key("incident", "active", incident_key))


# ===================================================================
# DISTRIBUTED LOCKS  (§6)
# ===================================================================


async def acquire_lock(
    r: Redis, lock_type: str, key: str, worker_id: str, ttl: int = 30
) -> bool:
    """Acquire a distributed lock. Returns True if acquired.

    lock_type: "window" or "correlation"
    """
    return bool(
        await r.set(_key("lock", lock_type, key), worker_id, nx=True, ex=ttl)
    )


async def release_lock(r: Redis, lock_type: str, key: str, worker_id: str) -> bool:
    """Release a lock only if still owned by this worker (compare-and-delete).

    Returns True if the lock was released.
    """
    lock_key = _key("lock", lock_type, key)
    # Lua script for atomic compare-and-delete
    script = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """
    result = await r.eval(script, 1, lock_key, worker_id)
    return bool(result)


# ===================================================================
# LAST SEEN  (§6)
# ===================================================================


async def set_last_seen(r: Redis, entity_key: str, ttl: int = 3600) -> None:
    """Track last log seen per entity."""
    now = datetime.now(timezone.utc).isoformat()
    await r.set(_key("last_seen", entity_key), now, ex=ttl)


async def get_last_seen(r: Redis, entity_key: str) -> str | None:
    """Get last seen timestamp for an entity."""
    return await r.get(_key("last_seen", entity_key))


# ===================================================================
# STREAMS  (§6, §9.3, §11.5)
# ===================================================================


async def ensure_consumer_group(
    r: Redis, stream: str, group: str
) -> None:
    """Create a consumer group on a stream (idempotent)."""
    try:
        await r.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception as e:
        # "BUSYGROUP Consumer Group name already exists"
        if "BUSYGROUP" not in str(e):
            raise


async def stream_add(
    r: Redis,
    stream: str,
    data: dict[str, str],
    *,
    maxlen: int | None = None,
) -> str:
    """Add an entry to a stream. Returns the entry ID."""
    kwargs: dict[str, Any] = {}
    if maxlen is not None:
        kwargs["maxlen"] = maxlen
        kwargs["approximate"] = True
    entry_id = await r.xadd(stream, data, **kwargs)
    return entry_id


async def stream_read_group(
    r: Redis,
    stream: str,
    group: str,
    consumer: str,
    *,
    count: int = 100,
    block: int = 2000,
) -> list[tuple[str, dict[str, str]]]:
    """Read entries from a stream via consumer group.

    Returns list of (entry_id, field_dict) tuples.
    """
    result = await r.xreadgroup(
        groupname=group,
        consumername=consumer,
        streams={stream: ">"},
        count=count,
        block=block,
    )
    if not result:
        return []
    # result is [[stream_name, [(id, fields), ...]]]
    entries = []
    for _stream_name, messages in result:
        for msg_id, fields in messages:
            entries.append((msg_id, fields))
    return entries


async def stream_ack(r: Redis, stream: str, group: str, *entry_ids: str) -> int:
    """Acknowledge stream entries."""
    if not entry_ids:
        return 0
    return await r.xack(stream, group, *entry_ids)


async def stream_len(r: Redis, stream: str) -> int:
    """Get the length of a stream."""
    return await r.xlen(stream)
