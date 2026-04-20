"""Kubernetes discovery service — namespaces + workload resources.

Powers the `/api/clusters/default/*` endpoints added in Task 11.8b. The
workflow builder UI calls these to populate namespace-picker and
deployment-name dropdowns so operators stop fat-fingering targets.

Design notes:
  - A single "default" cluster is assumed. The path shape
    `/clusters/{name}/...` already supports a multi-cluster future
    without breaking clients; a real `clusters` table comes later.
  - In-cluster config first, kubeconfig second. Matches the pattern in
    `app.temporal.activities` — keeps dev (kubeconfig) and prod
    (in-cluster SA token) paths identical.
  - Results cached in-memory for 30s. Cluster topology changes slowly;
    30s bounds operator-visible lag while killing the per-request API
    spam when several workflow-builder panels mount at once. Cache
    is keyed by (endpoint, include_system, namespace, kind) so filter
    variations are correctly isolated.
  - Returns plain dicts (no Pydantic) so the route layer owns the
    response schema. Timestamps are ISO-8601 UTC strings.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

# NOTE: kubernetes_asyncio is NOT imported at module top. It's a ~50-100MB
# memory footprint on first import, and the api pod's values-local.yaml
# memory limit (512Mi) can't absorb it on top of FastAPI + SQLAlchemy +
# httpx + pydantic + redis + temporalio. Lazy-importing inside _load_config
# keeps the api lean; the clusters endpoints pay the import cost only on
# first hit (~200ms one-off), which is fine for an interactive UI.
# Matches the pattern in app/temporal/activities.py.
_CACHE_TTL_SECONDS = 30.0
_cache: dict[tuple, tuple[float, Any]] = {}
_cache_lock = asyncio.Lock()

# Namespaces filtered out unless `include_system=True`. Matches the spirit of
# kubectl's system-namespace list plus the ones AutoMend itself creates.
_SYSTEM_NAMESPACE_PREFIXES = ("kube-",)
_SYSTEM_NAMESPACE_NAMES = frozenset({
    "automend",
    "logging",
    "kube-system",
    "kube-public",
    "kube-node-lease",
    "ingress-nginx",
    "cert-manager",
    "local-path-storage",
})


async def _load_config() -> Any:
    """Load the in-cluster SA credentials, falling back to ~/.kube/config.

    Lazy-imports kubernetes_asyncio on first call. Returns the `client`
    module so callers can instantiate API classes without re-importing.
    """
    from kubernetes_asyncio import client, config
    from kubernetes_asyncio.config import ConfigException

    try:
        config.load_incluster_config()
    except ConfigException:
        await config.load_kube_config()
    return client


def _iso(dt: Any) -> str | None:
    return dt.isoformat() if dt else None


def _is_system_namespace(name: str) -> bool:
    if name in _SYSTEM_NAMESPACE_NAMES:
        return True
    return any(name.startswith(p) for p in _SYSTEM_NAMESPACE_PREFIXES)


async def _cached(key: tuple, producer):
    """Return a cached value or call `producer()` and cache the result.

    Uses a shared lock so concurrent callers producing the same key don't
    stampede the K8s API — only the first call does work; the rest wait
    and read the cached result.
    """
    async with _cache_lock:
        now = time.monotonic()
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]
        value = await producer()
        _cache[key] = (now, value)
        return value


def _clear_cache() -> None:
    """Test hook — reset the module-level cache between tests."""
    _cache.clear()


async def list_namespaces(include_system: bool = False) -> list[dict[str, Any]]:
    """Return namespaces visible to the in-cluster SA.

    Requires `get/list/watch` on `namespaces` (cluster-scoped) — the
    ClusterRole rendered by Task 11.8a when `rbac.clusterWide: true` grants
    exactly this.
    """
    key = ("namespaces", include_system)

    async def _produce():
        client = await _load_config()
        v1 = client.CoreV1Api()
        try:
            resp = await v1.list_namespace()
        finally:
            await v1.api_client.close()
        out: list[dict[str, Any]] = []
        for ns in resp.items:
            name = ns.metadata.name
            if not include_system and _is_system_namespace(name):
                continue
            out.append({
                "name": name,
                "labels": dict(ns.metadata.labels or {}),
                "created_at": _iso(ns.metadata.creation_timestamp),
            })
        out.sort(key=lambda x: x["name"])
        return out

    return await _cached(key, _produce)


async def list_resources(namespace: str, kind: str) -> list[dict[str, Any]]:
    """Return workload resources of the given kind in the given namespace.

    Supported kinds: `deployment`, `statefulset`, `daemonset`, `pod`.
    """
    kind_lower = kind.lower()
    if kind_lower not in {"deployment", "statefulset", "daemonset", "pod"}:
        raise ValueError(
            f"Unsupported kind '{kind}'. Supported: deployment, statefulset, daemonset, pod."
        )

    key = ("resources", namespace, kind_lower)

    async def _produce():
        client = await _load_config()
        if kind_lower == "pod":
            core = client.CoreV1Api()
            try:
                resp = await core.list_namespaced_pod(namespace=namespace)
            finally:
                await core.api_client.close()
            return [
                {
                    "name": p.metadata.name,
                    "namespace": p.metadata.namespace,
                    "replicas": None,
                    "labels": dict(p.metadata.labels or {}),
                    "created_at": _iso(p.metadata.creation_timestamp),
                }
                for p in sorted(resp.items, key=lambda x: x.metadata.name)
            ]

        apps = client.AppsV1Api()
        try:
            if kind_lower == "deployment":
                resp = await apps.list_namespaced_deployment(namespace=namespace)
            elif kind_lower == "statefulset":
                resp = await apps.list_namespaced_stateful_set(namespace=namespace)
            else:  # daemonset
                resp = await apps.list_namespaced_daemon_set(namespace=namespace)
        finally:
            await apps.api_client.close()

        out = []
        for item in sorted(resp.items, key=lambda x: x.metadata.name):
            # DaemonSet doesn't have a spec.replicas — use status.desiredNumberScheduled.
            if kind_lower == "daemonset":
                replicas = getattr(item.status, "desired_number_scheduled", None)
            else:
                replicas = getattr(item.spec, "replicas", None)
            out.append({
                "name": item.metadata.name,
                "namespace": item.metadata.namespace,
                "replicas": replicas,
                "labels": dict(item.metadata.labels or {}),
                "created_at": _iso(item.metadata.creation_timestamp),
            })
        return out

    return await _cached(key, _produce)
