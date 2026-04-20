"""Embedding service — converts text to vector embeddings for semantic search (§14).

Default: OpenAI text-embedding-3-small (1536 dimensions).
When no API key is configured, returns zero vectors for dev/testing.
"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Wraps the embedding model API (OpenAI-compatible)."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.embedding_api_key
        self.base_url = base_url or settings.embedding_api_base_url
        self.model = model or settings.embedding_model
        self.dimensions = dimensions or settings.embedding_dimensions

    @property
    def _is_configured(self) -> bool:
        return bool(self.api_key and self.api_key not in ("", "sk-..."))

    async def embed(self, text: str) -> list[float]:
        """Embed a single text string. Returns vector of configured dimensions."""
        if not self._is_configured:
            logger.debug("Embedding API key not configured, returning zero vector")
            return [0.0] * self.dimensions

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "input": text,
                    "dimensions": self.dimensions,
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts in a single API call."""
        if not texts:
            return []

        if not self._is_configured:
            logger.debug("Embedding API key not configured, returning zero vectors")
            return [[0.0] * self.dimensions for _ in texts]

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "input": texts,
                    "dimensions": self.dimensions,
                },
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            # Sort by index to maintain order
            return [
                item["embedding"]
                for item in sorted(data["data"], key=lambda x: x["index"])
            ]
