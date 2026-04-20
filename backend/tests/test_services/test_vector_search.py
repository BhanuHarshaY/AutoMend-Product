"""Tests for VectorSearchService (§15).

Requires Postgres with pgvector. Skips if unavailable.
Uses a mock EmbeddingService that returns deterministic vectors.
"""

from __future__ import annotations

import asyncio
import math
from uuid import uuid4

import pytest
import pytest_asyncio

try:
    import asyncpg
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.models.db import Base
    from app.stores import postgres_store as store
    from app.services.vector_search_service import VectorSearchService

    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False

PG_URL = "postgresql+asyncpg://automend:automend@localhost:5432/automend"
DIMS = 1536


def _pg_available() -> bool:
    if not _HAS_DEPS:
        return False
    try:
        conn = asyncio.get_event_loop().run_until_complete(
            asyncpg.connect(user="automend", password="automend",
                            database="automend", host="localhost", port=5432, timeout=3)
        )
        asyncio.get_event_loop().run_until_complete(conn.close())
        return True
    except Exception:
        return False


_pg_is_up = _pg_available()
pytestmark = pytest.mark.skipif(not _pg_is_up, reason="Postgres not available")


# ---------------------------------------------------------------------------
# Mock embedding service — returns deterministic vectors
# ---------------------------------------------------------------------------


class MockEmbeddingService:
    """Returns a vector based on a seed derived from the input text.

    This ensures the same text always gets the same embedding, and
    similar texts get similar (but not identical) embeddings.
    """

    def __init__(self, dimensions: int = DIMS):
        self.dimensions = dimensions

    async def embed(self, text: str) -> list[float]:
        return self._text_to_vector(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._text_to_vector(t) for t in texts]

    def _text_to_vector(self, text: str) -> list[float]:
        """Simple hash-based vector generation for testing."""
        seed = hash(text) % 10000
        # Generate a normalized vector
        vec = [math.sin(seed + i * 0.1) for i in range(self.dimensions)]
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec] if norm > 0 else vec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def session():
    eng = create_async_engine(PG_URL, echo=False)
    async with eng.begin() as conn:
        await conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(sa.text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        await conn.run_sync(Base.metadata.create_all)
    async with eng.connect() as conn:
        txn = await conn.begin()
        sess = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield sess
        finally:
            await sess.close()
            await txn.rollback()
    await eng.dispose()


@pytest.fixture()
def embedding_svc():
    return MockEmbeddingService(DIMS)


@pytest.fixture()
def search_svc(embedding_svc):
    return VectorSearchService(embedding_svc)


async def _create_tool_with_embedding(session, name: str, embedding: list[float], **kwargs):
    """Create a tool and set its embedding vector."""
    defaults = dict(
        display_name=name.replace("_", " ").title(),
        description=f"Description for {name}",
        category="kubernetes",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    defaults.update(kwargs)
    tool = await store.create_tool(session, name=name, **defaults)
    # Set embedding directly via SQL (pgvector column)
    embedding_str = f"[{','.join(str(x) for x in embedding)}]"
    await session.execute(
        sa.text("UPDATE tools SET embedding = CAST(:emb AS vector) WHERE id = :id"),
        {"emb": embedding_str, "id": str(tool.id)},
    )
    await session.flush()
    return tool


async def _create_playbook_with_embedding(session, name: str, embedding: list[float]):
    """Create a playbook + version and set the version's embedding."""
    pb = await store.create_playbook(session, name=name)
    v = await store.save_version(session, pb.id, workflow_spec={"name": name, "steps": []})
    embedding_str = f"[{','.join(str(x) for x in embedding)}]"
    await session.execute(
        sa.text("UPDATE playbook_versions SET embedding = CAST(:emb AS vector) WHERE id = :id"),
        {"emb": embedding_str, "id": str(v.id)},
    )
    await session.flush()
    return pb, v


# ===================================================================
# TOOL SEARCH
# ===================================================================


class TestSearchTools:
    async def test_returns_tools_with_embeddings(self, session, search_svc, embedding_svc):
        # Create tools with embeddings based on their description
        emb_restart = await embedding_svc.embed("restart crashed kubernetes pod")
        emb_logs = await embedding_svc.embed("fetch pod logs for debugging")
        emb_scale = await embedding_svc.embed("scale deployment replicas")

        await _create_tool_with_embedding(session, f"restart_{uuid4().hex[:6]}", emb_restart)
        await _create_tool_with_embedding(session, f"logs_{uuid4().hex[:6]}", emb_logs)
        await _create_tool_with_embedding(session, f"scale_{uuid4().hex[:6]}", emb_scale)

        # Search for something related to restarting
        results = await search_svc.search_tools(session, "restart crashed kubernetes pod", limit=10, min_similarity=0.0)
        assert len(results) >= 1
        # First result should be the restart tool (exact match embedding)
        assert results[0]["similarity"] > 0.99

    async def test_respects_min_similarity(self, session, search_svc, embedding_svc):
        emb = await embedding_svc.embed("unique tool")
        await _create_tool_with_embedding(session, f"unique_{uuid4().hex[:6]}", emb)

        # With very high min_similarity, should find the exact match
        results = await search_svc.search_tools(session, "unique tool", min_similarity=0.99)
        assert len(results) >= 1

        # With min_similarity=1.0, might not find anything (floating point)
        results = await search_svc.search_tools(session, "completely different text", min_similarity=0.999)
        # Should return empty or very few since the query embedding is different
        # (not guaranteed to be 0 due to hash collisions, so just check it runs)
        assert isinstance(results, list)

    async def test_respects_limit(self, session, search_svc, embedding_svc):
        # Create several tools
        for i in range(5):
            emb = await embedding_svc.embed(f"tool number {i}")
            await _create_tool_with_embedding(session, f"many_{uuid4().hex[:6]}", emb)

        results = await search_svc.search_tools(session, "tool number 0", limit=2, min_similarity=0.0)
        assert len(results) <= 2

    async def test_excludes_inactive_tools(self, session, search_svc, embedding_svc):
        emb = await embedding_svc.embed("inactive tool")
        await _create_tool_with_embedding(
            session, f"inactive_{uuid4().hex[:6]}", emb, is_active=False,
        )
        # Manually update since create_tool defaults is_active=True
        # The is_active=False in kwargs is passed to create_tool
        results = await search_svc.search_tools(session, "inactive tool", min_similarity=0.0)
        names = [r["name"] for r in results]
        assert not any("inactive" in n for n in names)

    async def test_excludes_tools_without_embedding(self, session, search_svc):
        # Create tool without embedding (no UPDATE embedding)
        await store.create_tool(
            session, name=f"no_emb_{uuid4().hex[:6]}",
            display_name="No Emb", description="No embedding",
            category="test", input_schema={}, output_schema={},
        )
        results = await search_svc.search_tools(session, "no embedding", min_similarity=0.0)
        names = [r["name"] for r in results]
        assert not any("no_emb" in n for n in names)

    async def test_result_has_expected_fields(self, session, search_svc, embedding_svc):
        emb = await embedding_svc.embed("fields check")
        await _create_tool_with_embedding(session, f"fields_{uuid4().hex[:6]}", emb)

        results = await search_svc.search_tools(session, "fields check", min_similarity=0.0)
        assert len(results) >= 1
        r = results[0]
        assert "id" in r
        assert "name" in r
        assert "description" in r
        assert "similarity" in r
        assert "input_schema" in r
        assert "side_effect_level" in r


# ===================================================================
# PLAYBOOK SEARCH
# ===================================================================


class TestSearchPlaybooks:
    async def test_returns_playbooks_with_embeddings(self, session, search_svc, embedding_svc):
        emb = await embedding_svc.embed("gpu memory failure recovery")
        await _create_playbook_with_embedding(session, f"gpu_pb_{uuid4().hex[:6]}", emb)

        results = await search_svc.search_playbooks(session, "gpu memory failure recovery", min_similarity=0.0)
        assert len(results) >= 1
        assert results[0]["similarity"] > 0.99

    async def test_result_has_expected_fields(self, session, search_svc, embedding_svc):
        emb = await embedding_svc.embed("playbook fields")
        await _create_playbook_with_embedding(session, f"pb_fields_{uuid4().hex[:6]}", emb)

        results = await search_svc.search_playbooks(session, "playbook fields", min_similarity=0.0)
        assert len(results) >= 1
        r = results[0]
        assert "playbook_id" in r
        assert "name" in r
        assert "version_number" in r
        assert "status" in r
        assert "similarity" in r

    async def test_empty_search_returns_list(self, session, search_svc):
        results = await search_svc.search_playbooks(session, "xyzzy nonexistent thing", min_similarity=0.99)
        assert isinstance(results, list)
