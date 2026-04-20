"""Tests for the EmbeddingService (§14).

Uses httpx mock transport — no external API calls needed.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.embedding_service import EmbeddingService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_embedding(dim: int = 1536) -> list[float]:
    """Generate a fake embedding vector."""
    return [float(i) / dim for i in range(dim)]


def _mock_openai_response(embeddings: list[list[float]]) -> dict:
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": i, "embedding": emb}
            for i, emb in enumerate(embeddings)
        ],
        "model": "text-embedding-3-small",
        "usage": {"prompt_tokens": 10, "total_tokens": 10},
    }


# ---------------------------------------------------------------------------
# Fallback (no API key) tests
# ---------------------------------------------------------------------------


class TestFallbackBehavior:
    async def test_embed_returns_zero_vector_without_key(self):
        svc = EmbeddingService(api_key="", dimensions=1536)
        result = await svc.embed("test text")
        assert len(result) == 1536
        assert all(v == 0.0 for v in result)

    async def test_embed_batch_returns_zero_vectors_without_key(self):
        svc = EmbeddingService(api_key="", dimensions=1536)
        result = await svc.embed_batch(["text 1", "text 2", "text 3"])
        assert len(result) == 3
        assert all(len(v) == 1536 for v in result)
        assert all(all(x == 0.0 for x in v) for v in result)

    async def test_embed_batch_empty_list(self):
        svc = EmbeddingService(api_key="", dimensions=1536)
        result = await svc.embed_batch([])
        assert result == []

    async def test_is_configured_false_for_empty_key(self):
        svc = EmbeddingService(api_key="")
        assert svc._is_configured is False

    async def test_is_configured_false_for_placeholder(self):
        svc = EmbeddingService(api_key="sk-...")
        assert svc._is_configured is False

    async def test_is_configured_true_for_real_key(self):
        svc = EmbeddingService(api_key="sk-real-key-here")
        assert svc._is_configured is True


# ---------------------------------------------------------------------------
# API call tests (mocked httpx)
# ---------------------------------------------------------------------------


class TestEmbedSingle:
    async def test_calls_openai_api(self):
        expected_embedding = _mock_embedding(1536)

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["model"] == "text-embedding-3-small"
            assert body["input"] == "restart crashed pods"
            assert body["dimensions"] == 1536
            assert "Bearer sk-test" in request.headers["authorization"]
            return httpx.Response(200, json=_mock_openai_response([expected_embedding]))

        transport = httpx.MockTransport(handler)
        svc = EmbeddingService(api_key="sk-test", base_url="http://mock", dimensions=1536)

        # Patch httpx.AsyncClient to use mock transport
        original_init = httpx.AsyncClient.__init__

        def patched_init(self_client, **kwargs):
            original_init(self_client, transport=transport, **kwargs)

        httpx.AsyncClient.__init__ = patched_init
        try:
            result = await svc.embed("restart crashed pods")
        finally:
            httpx.AsyncClient.__init__ = original_init

        assert result == expected_embedding
        assert len(result) == 1536


class TestEmbedBatch:
    async def test_batch_preserves_order(self):
        emb_0 = [1.0] * 10
        emb_1 = [2.0] * 10
        emb_2 = [3.0] * 10

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert isinstance(body["input"], list)
            assert len(body["input"]) == 3
            # Return in shuffled order to test sorting
            return httpx.Response(200, json={
                "data": [
                    {"index": 2, "embedding": emb_2},
                    {"index": 0, "embedding": emb_0},
                    {"index": 1, "embedding": emb_1},
                ],
            })

        transport = httpx.MockTransport(handler)
        svc = EmbeddingService(api_key="sk-test", base_url="http://mock", dimensions=10)

        original_init = httpx.AsyncClient.__init__

        def patched_init(self_client, **kwargs):
            original_init(self_client, transport=transport, **kwargs)

        httpx.AsyncClient.__init__ = patched_init
        try:
            result = await svc.embed_batch(["a", "b", "c"])
        finally:
            httpx.AsyncClient.__init__ = original_init

        assert result[0] == emb_0
        assert result[1] == emb_1
        assert result[2] == emb_2


# ---------------------------------------------------------------------------
# Init / config tests
# ---------------------------------------------------------------------------


class TestServiceInit:
    def test_defaults_from_settings(self):
        svc = EmbeddingService()
        assert svc.model == "text-embedding-3-small"
        assert svc.dimensions == 1536

    def test_custom_overrides(self):
        svc = EmbeddingService(
            api_key="k", base_url="http://custom", model="custom-model", dimensions=384,
        )
        assert svc.api_key == "k"
        assert svc.base_url == "http://custom"
        assert svc.model == "custom-model"
        assert svc.dimensions == 384
