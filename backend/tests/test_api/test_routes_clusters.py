"""Tests for the clusters discovery routes (Task 11.8b).

Covers the service layer (filter logic, caching) plus the route layer
(auth gate, error mapping). Kubernetes API calls are mocked — tests do
NOT require a live cluster.

Runs without Postgres: auth is bypassed via `app.dependency_overrides`
instead of the seed-user-via-psycopg2 pattern used by the DB-backed
route tests. Keeps the Day-2 Clusters API independently testable in CI.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from fastapi.testclient import TestClient

    from app.dependencies import require_role
    from app.services import k8s_client
    from main_api import create_app

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False


pytestmark = pytest.mark.skipif(not _HAS_DEPS, reason="backend deps not installed")


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _ns(name: str, labels: dict[str, str] | None = None, created: str | None = None):
    ts = datetime.fromisoformat(created) if created else datetime(2026, 4, 1, tzinfo=timezone.utc)
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            labels=labels or {},
            creation_timestamp=ts,
        ),
    )


def _deploy(name: str, namespace: str, replicas: int = 1, labels: dict[str, str] | None = None):
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace=namespace,
            labels=labels or {},
            creation_timestamp=datetime(2026, 4, 10, tzinfo=timezone.utc),
        ),
        spec=SimpleNamespace(replicas=replicas),
        status=SimpleNamespace(),
    )


def _fake_core_v1(namespaces: list | None = None, pods: list | None = None):
    api = MagicMock()
    api.list_namespace = AsyncMock(return_value=SimpleNamespace(items=namespaces or []))
    api.list_namespaced_pod = AsyncMock(return_value=SimpleNamespace(items=pods or []))
    api.api_client = SimpleNamespace(close=AsyncMock())
    return api


def _fake_apps_v1(deployments: list | None = None, statefulsets: list | None = None, daemonsets: list | None = None):
    api = MagicMock()
    api.list_namespaced_deployment = AsyncMock(return_value=SimpleNamespace(items=deployments or []))
    api.list_namespaced_stateful_set = AsyncMock(return_value=SimpleNamespace(items=statefulsets or []))
    api.list_namespaced_daemon_set = AsyncMock(return_value=SimpleNamespace(items=daemonsets or []))
    api.api_client = SimpleNamespace(close=AsyncMock())
    return api


def _fake_client_module(core=None, apps=None):
    """Build a stand-in for the kubernetes_asyncio.client module.

    `_load_config` is lazy-imported; tests patch it to return a fake module
    with `.CoreV1Api` / `.AppsV1Api` callables that yield pre-canned mocks.
    """
    return SimpleNamespace(
        CoreV1Api=lambda: core,
        AppsV1Api=lambda: apps,
    )


@pytest.fixture(autouse=True)
def _clear_k8s_cache():
    """Every test starts with an empty cache so caching tests don't leak."""
    k8s_client._clear_cache()
    yield
    k8s_client._clear_cache()


@pytest.fixture()
def client_as_editor():
    """TestClient where the require_role("editor") dep is satisfied."""
    app = create_app()
    # Override both possible dependency keys (require_role returns a fresh
    # closure each call — we need the exact dep object created at import time).
    from app.api import routes_clusters as rc

    # Find the exact dep objects used by the clusters routes.
    for route in rc.router.routes:
        for dep in getattr(route, "dependant", SimpleNamespace(dependencies=[])).dependencies:
            if dep.call is not None and getattr(dep.call, "__qualname__", "").startswith(
                "require_role"
            ):
                app.dependency_overrides[dep.call] = lambda: {
                    "sub": "editor@test",
                    "role": "editor",
                }
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def client_as_viewer():
    """TestClient where the require_role("editor") dep always rejects.

    Simulates a viewer-role JWT hitting the endpoint — the actual decorator
    raises HTTPException(403), which is what we want to assert.
    """
    app = create_app()
    from fastapi import HTTPException, status

    from app.api import routes_clusters as rc

    def _deny():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    for route in rc.router.routes:
        for dep in getattr(route, "dependant", SimpleNamespace(dependencies=[])).dependencies:
            if dep.call is not None and getattr(dep.call, "__qualname__", "").startswith(
                "require_role"
            ):
                app.dependency_overrides[dep.call] = _deny
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ===================================================================
# Service layer — k8s_client.list_namespaces
# ===================================================================


class TestListNamespacesService:
    @pytest.mark.asyncio
    async def test_filters_system_namespaces_by_default(self):
        core = _fake_core_v1(namespaces=[
            _ns("ml"),
            _ns("kube-system"),
            _ns("automend"),
            _ns("default"),
            _ns("ingress-nginx"),
        ])
        with patch.object(k8s_client, "_load_config", new=AsyncMock(return_value=_fake_client_module(core=core))):
            result = await k8s_client.list_namespaces()
        names = {n["name"] for n in result}
        assert names == {"ml", "default"}

    @pytest.mark.asyncio
    async def test_include_system_returns_all(self):
        core = _fake_core_v1(namespaces=[
            _ns("ml"),
            _ns("kube-system"),
            _ns("automend"),
        ])
        with patch.object(k8s_client, "_load_config", new=AsyncMock(return_value=_fake_client_module(core=core))):
            result = await k8s_client.list_namespaces(include_system=True)
        assert {n["name"] for n in result} == {"ml", "kube-system", "automend"}

    @pytest.mark.asyncio
    async def test_returns_labels_and_timestamps(self):
        core = _fake_core_v1(namespaces=[
            _ns("ml", labels={"team": "mlops"}, created="2026-03-15T10:00:00+00:00"),
        ])
        with patch.object(k8s_client, "_load_config", new=AsyncMock(return_value=_fake_client_module(core=core))):
            result = await k8s_client.list_namespaces()
        assert result[0]["labels"] == {"team": "mlops"}
        assert result[0]["created_at"] == "2026-03-15T10:00:00+00:00"

    @pytest.mark.asyncio
    async def test_results_are_sorted_by_name(self):
        core = _fake_core_v1(namespaces=[_ns("payments"), _ns("ml"), _ns("search")])
        with patch.object(k8s_client, "_load_config", new=AsyncMock(return_value=_fake_client_module(core=core))):
            result = await k8s_client.list_namespaces()
        assert [n["name"] for n in result] == ["ml", "payments", "search"]

    @pytest.mark.asyncio
    async def test_caches_repeated_calls(self):
        core = _fake_core_v1(namespaces=[_ns("ml")])
        with patch.object(k8s_client, "_load_config", new=AsyncMock(return_value=_fake_client_module(core=core))):
            await k8s_client.list_namespaces()
            await k8s_client.list_namespaces()
            await k8s_client.list_namespaces()
        # Three user calls → exactly one upstream API call.
        assert core.list_namespace.await_count == 1

    @pytest.mark.asyncio
    async def test_filter_variants_cached_independently(self):
        core = _fake_core_v1(namespaces=[_ns("ml"), _ns("kube-system")])
        with patch.object(k8s_client, "_load_config", new=AsyncMock(return_value=_fake_client_module(core=core))):
            # Different include_system → two different cache keys → two API calls.
            r1 = await k8s_client.list_namespaces(include_system=False)
            r2 = await k8s_client.list_namespaces(include_system=True)
        assert core.list_namespace.await_count == 2
        assert len(r1) == 1  # ml only
        assert len(r2) == 2  # ml + kube-system


# ===================================================================
# Service layer — k8s_client.list_resources
# ===================================================================


class TestListResourcesService:
    @pytest.mark.asyncio
    async def test_lists_deployments(self):
        apps = _fake_apps_v1(deployments=[
            _deploy("reco-pod", "ml", replicas=3, labels={"app": "reco-pod"}),
            _deploy("fraud-scoring", "ml", replicas=2),
        ])
        with patch.object(k8s_client, "_load_config", new=AsyncMock(return_value=_fake_client_module(apps=apps))):
            result = await k8s_client.list_resources("ml", "deployment")
        assert len(result) == 2
        # Sorted alphabetically by name.
        assert [r["name"] for r in result] == ["fraud-scoring", "reco-pod"]
        reco = next(r for r in result if r["name"] == "reco-pod")
        assert reco["namespace"] == "ml"
        assert reco["replicas"] == 3
        assert reco["labels"] == {"app": "reco-pod"}

    @pytest.mark.asyncio
    async def test_kind_is_case_insensitive(self):
        apps = _fake_apps_v1(deployments=[_deploy("x", "ml")])
        with patch.object(k8s_client, "_load_config", new=AsyncMock(return_value=_fake_client_module(apps=apps))):
            await k8s_client.list_resources("ml", "Deployment")
            await k8s_client.list_resources("ml", "DEPLOYMENT")
        # Case-insensitive → same cache key → one upstream call.
        assert apps.list_namespaced_deployment.await_count == 1

    @pytest.mark.asyncio
    async def test_unsupported_kind_raises_value_error(self):
        with pytest.raises(ValueError, match="Unsupported kind"):
            await k8s_client.list_resources("ml", "service")

    @pytest.mark.asyncio
    async def test_daemonset_uses_desired_scheduled(self):
        ds = SimpleNamespace(
            metadata=SimpleNamespace(name="fluent-bit", namespace="logging", labels={},
                                     creation_timestamp=datetime(2026, 4, 1, tzinfo=timezone.utc)),
            spec=SimpleNamespace(),
            status=SimpleNamespace(desired_number_scheduled=5),
        )
        apps = _fake_apps_v1(daemonsets=[ds])
        with patch.object(k8s_client, "_load_config", new=AsyncMock(return_value=_fake_client_module(apps=apps))):
            result = await k8s_client.list_resources("logging", "daemonset")
        assert result[0]["replicas"] == 5


# ===================================================================
# Route layer — /api/clusters
# ===================================================================


class TestNamespacesRoute:
    def test_happy_path(self, client_as_editor):
        async def fake_list(include_system=False):
            return [{"name": "ml", "labels": {}, "created_at": None}]

        with patch.object(k8s_client, "list_namespaces", side_effect=fake_list):
            resp = client_as_editor.get("/api/clusters/default/namespaces")
        assert resp.status_code == 200
        assert resp.json() == [{"name": "ml", "labels": {}, "created_at": None}]

    def test_include_system_flag_passed_through(self, client_as_editor):
        seen_flags: list[bool] = []

        async def fake_list(include_system=False):
            seen_flags.append(include_system)
            return []

        with patch.object(k8s_client, "list_namespaces", side_effect=fake_list):
            client_as_editor.get("/api/clusters/default/namespaces")
            client_as_editor.get("/api/clusters/default/namespaces?include_system=true")
        assert seen_flags == [False, True]

    def test_unknown_cluster_returns_404(self, client_as_editor):
        resp = client_as_editor.get("/api/clusters/prod-west/namespaces")
        assert resp.status_code == 404

    def test_k8s_error_maps_to_502(self, client_as_editor):
        async def boom(include_system=False):
            raise RuntimeError("connection refused")

        with patch.object(k8s_client, "list_namespaces", side_effect=boom):
            resp = client_as_editor.get("/api/clusters/default/namespaces")
        assert resp.status_code == 502
        assert "connection refused" in resp.json()["detail"]

    def test_viewer_role_forbidden(self, client_as_viewer):
        resp = client_as_viewer.get("/api/clusters/default/namespaces")
        assert resp.status_code == 403


class TestResourcesRoute:
    def test_happy_path(self, client_as_editor):
        async def fake_list(namespace, kind):
            return [{
                "name": "reco-pod",
                "namespace": namespace,
                "replicas": 3,
                "labels": {},
                "created_at": None,
            }]

        with patch.object(k8s_client, "list_resources", side_effect=fake_list):
            resp = client_as_editor.get("/api/clusters/default/namespaces/ml/resources?kind=deployment")
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["name"] == "reco-pod"
        assert data[0]["namespace"] == "ml"
        assert data[0]["replicas"] == 3

    def test_defaults_kind_to_deployment(self, client_as_editor):
        seen: list[str] = []

        async def fake_list(namespace, kind):
            seen.append(kind)
            return []

        with patch.object(k8s_client, "list_resources", side_effect=fake_list):
            client_as_editor.get("/api/clusters/default/namespaces/ml/resources")
        assert seen == ["deployment"]

    def test_unsupported_kind_returns_422(self, client_as_editor):
        async def fake_list(namespace, kind):
            raise ValueError("Unsupported kind 'service'")

        with patch.object(k8s_client, "list_resources", side_effect=fake_list):
            resp = client_as_editor.get("/api/clusters/default/namespaces/ml/resources?kind=service")
        assert resp.status_code == 422
        assert "Unsupported kind" in resp.json()["detail"]

    def test_k8s_error_maps_to_502(self, client_as_editor):
        async def boom(namespace, kind):
            raise RuntimeError("forbidden")

        with patch.object(k8s_client, "list_resources", side_effect=boom):
            resp = client_as_editor.get("/api/clusters/default/namespaces/ml/resources?kind=deployment")
        assert resp.status_code == 502

    def test_unknown_cluster_returns_404(self, client_as_editor):
        resp = client_as_editor.get("/api/clusters/prod-west/namespaces/ml/resources")
        assert resp.status_code == 404
