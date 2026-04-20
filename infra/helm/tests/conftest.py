"""Shared fixtures for the AutoMend Helm chart test suite.

These tests run offline — they invoke the `helm` CLI to lint + render the
chart, then parse the output YAML to assert structural invariants. No live
cluster required. Skips cleanly when `helm` isn't installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# helm binary resolution
# ---------------------------------------------------------------------------

# Windows winget installs `helm.exe` under this path but doesn't always add it
# to the shell PATH in non-default shells (e.g. git-bash).
_WINGET_HELM = Path(
    os.path.expanduser("~")
) / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / "helm.exe"


def _find_helm() -> str | None:
    """Return an absolute path to the helm binary, or None if not found."""
    on_path = shutil.which("helm")
    if on_path:
        return on_path
    if _WINGET_HELM.exists():
        return str(_WINGET_HELM)
    return None


HELM = _find_helm()

pytestmark = pytest.mark.skipif(
    HELM is None,
    reason="helm binary not found on PATH or at the winget default location",
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# The chart lives at <repo-root>/infra/helm/automend. This conftest is at
# <repo-root>/infra/helm/tests/conftest.py — walk up to find the chart.
CHART_DIR = Path(__file__).resolve().parent.parent / "automend"
VALUES_LOCAL = CHART_DIR / "values-local.yaml"
VALUES_DEFAULT = CHART_DIR / "values.yaml"


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


# ---------------------------------------------------------------------------
# Rendered-chart fixtures (session-scoped — each render takes ~1s)
# ---------------------------------------------------------------------------


def _render(*extra_args: str) -> list[dict]:
    """Run `helm template` and return the parsed YAML documents."""
    assert HELM is not None
    result = _run([
        HELM, "template", "automend", str(CHART_DIR),
        *extra_args,
    ])
    if result.returncode != 0:
        pytest.fail(
            f"helm template failed:\n"
            f"STDOUT: {result.stdout}\n"
            f"STDERR: {result.stderr}"
        )
    return [d for d in yaml.safe_load_all(result.stdout) if d]


@pytest.fixture(scope="session")
def rendered_local() -> list[dict]:
    """Chart rendered with values-local.yaml (subcharts on, kind defaults)."""
    return _render("-f", str(VALUES_LOCAL))


@pytest.fixture(scope="session")
def rendered_external() -> list[dict]:
    """Default values.yaml + external host overrides (simulates Phase 12 GCP)."""
    return _render(
        "--set", "external.postgres.host=pg.example.com",
        "--set", "external.redis.host=redis.example.com",
        "--set", "external.temporal.serverUrl=temporal.example.com:7233",
    )


# ---------------------------------------------------------------------------
# Helpers for navigating rendered resources
# ---------------------------------------------------------------------------


def by_kind(docs: list[dict], kind: str) -> list[dict]:
    return [d for d in docs if d.get("kind") == kind]


def by_component(docs: list[dict], kind: str, component: str) -> dict | None:
    for d in docs:
        if d.get("kind") != kind:
            continue
        labels = d.get("metadata", {}).get("labels", {}) or {}
        if labels.get("app.kubernetes.io/component") == component:
            return d
    return None
