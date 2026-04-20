"""Shared pytest fixtures for the Track A classifier service tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
import torch


# ---------------------------------------------------------------------------
# Sample log windows
# ---------------------------------------------------------------------------


@pytest.fixture()
def oom_logs() -> list[dict[str, Any]]:
    """A realistic memory-exhaustion log window."""
    return [
        {"body": "pod recommendation-v2 OOMKilled", "attributes": {"namespace": "prod"}},
        {"body": "container out of memory, exit code 137", "attributes": {}},
        {"body": "cgroup memory limit exceeded for pod reco-xyz", "attributes": {}},
    ]


@pytest.fixture()
def gpu_logs() -> list[dict[str, Any]]:
    return [
        {"body": "CUDA error: device-side assert triggered", "attributes": {}},
        {"body": "NVML: Xid (PCI:0000:03:00): 31", "attributes": {}},
    ]


@pytest.fixture()
def network_logs() -> list[dict[str, Any]]:
    return [
        {"body": "connection refused to upstream service", "attributes": {}},
        {"body": "DNS resolution failed for auth.internal", "attributes": {}},
    ]


@pytest.fixture()
def empty_body_logs() -> list[dict[str, Any]]:
    """Logs whose bodies are blank or missing — should classify as Normal."""
    return [
        {"body": "", "attributes": {}},
        {"attributes": {"x": 1}},
        {"body": "   "},
    ]


# ---------------------------------------------------------------------------
# Mock model + tokenizer that don't touch HuggingFace
# ---------------------------------------------------------------------------


def _make_mock_model(logits: list[float]) -> MagicMock:
    """Build a mock model whose forward pass returns the given logits."""
    model = MagicMock()
    output = MagicMock()
    output.logits = torch.tensor([logits])
    model.return_value = output
    return model


def _make_mock_tokenizer() -> MagicMock:
    """Mock tokenizer that returns dummy tensors of the right shape."""
    def _call(text: str, **kwargs):
        # 1x512 input_ids of zeros is enough — the mock model ignores them.
        return {
            "input_ids": torch.zeros((1, 512), dtype=torch.long),
            "attention_mask": torch.ones((1, 512), dtype=torch.long),
        }

    tokenizer = MagicMock(side_effect=_call)
    return tokenizer


@pytest.fixture()
def mock_model_factory():
    """Factory to build a mock model with arbitrary logits."""
    return _make_mock_model


@pytest.fixture()
def mock_tokenizer():
    return _make_mock_tokenizer()


@pytest.fixture()
def cpu_device() -> torch.device:
    return torch.device("cpu")
