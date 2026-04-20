"""Cluster discovery routes (Task 11.8b).

GET /api/clusters/{cluster}/namespaces
    List namespaces visible to the app ServiceAccount. Filters system
    namespaces by default; `?include_system=true` disables the filter.

GET /api/clusters/{cluster}/namespaces/{ns}/resources?kind=deployment
    List workload resources of a given kind in the given namespace.
    Supported kinds: deployment, statefulset, daemonset, pod.

Both endpoints require the `editor` role (read-only but not public — the
namespace list is cluster-shape information some environments prefer not
to expose to every authenticated session).

The `{cluster}` path parameter is accepted for forward-compatibility with
a future multi-cluster setup; for now only "default" resolves to the
in-cluster K8s API. Any other value returns 404.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel

from app.dependencies import require_role
from app.services import k8s_client

router = APIRouter()


_DEFAULT_CLUSTER = "default"


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class Namespace(BaseModel):
    name: str
    labels: dict[str, str] = {}
    created_at: str | None = None


class Resource(BaseModel):
    name: str
    namespace: str
    replicas: int | None = None
    labels: dict[str, str] = {}
    created_at: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_cluster(cluster: str) -> None:
    if cluster != _DEFAULT_CLUSTER:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown cluster '{cluster}'. Only 'default' is supported.",
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/{cluster}/namespaces", response_model=list[Namespace])
async def list_namespaces(
    cluster: str = Path(..., description="Cluster name; only 'default' is supported for now"),
    include_system: bool = Query(
        False, description="Include kube-*/automend/ingress-nginx/etc."
    ),
    _user: dict = Depends(require_role("editor")),
) -> list[dict[str, Any]]:
    _check_cluster(cluster)
    try:
        return await k8s_client.list_namespaces(include_system=include_system)
    except Exception as e:
        # Surface K8s API errors as 502 — the downstream cluster is a dependency,
        # not an input validation problem.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to list namespaces: {e}",
        ) from e


@router.get(
    "/{cluster}/namespaces/{ns}/resources",
    response_model=list[Resource],
)
async def list_resources(
    cluster: str = Path(...),
    ns: str = Path(..., description="Namespace to query"),
    kind: str = Query("deployment", description="Resource kind"),
    _user: dict = Depends(require_role("editor")),
) -> list[dict[str, Any]]:
    _check_cluster(cluster)
    try:
        return await k8s_client.list_resources(namespace=ns, kind=kind)
    except ValueError as e:
        # Unsupported kind → 422 (client error, fixable by changing ?kind=)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to list {kind}s in namespace '{ns}': {e}",
        ) from e
