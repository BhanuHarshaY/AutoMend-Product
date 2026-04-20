"""Vector search service using pgvector in Postgres (§15).

Provides semantic search over tools and playbook versions using
cosine distance on pre-computed embeddings.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.embedding_service import EmbeddingService


class VectorSearchService:
    """Semantic search using pgvector cosine distance."""

    def __init__(self, embedding_service: EmbeddingService) -> None:
        self.embedding_service = embedding_service

    async def search_tools(
        self,
        db: AsyncSession,
        query: str,
        limit: int = 10,
        min_similarity: float = 0.5,
    ) -> list[dict]:
        """Search tools by semantic similarity to a natural-language query."""
        query_embedding = await self.embedding_service.embed(query)
        embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"

        result = await db.execute(
            text("""
                SELECT
                    id, name, display_name, description, category,
                    input_schema, output_schema, side_effect_level,
                    required_approvals, environments_allowed,
                    1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
                FROM tools
                WHERE is_active = true
                  AND embedding IS NOT NULL
                  AND 1 - (embedding <=> CAST(:embedding AS vector)) >= :min_similarity
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT :limit
            """),
            {
                "embedding": embedding_str,
                "min_similarity": min_similarity,
                "limit": limit,
            },
        )
        return [dict(row._mapping) for row in result.fetchall()]

    async def search_playbooks(
        self,
        db: AsyncSession,
        query: str,
        limit: int = 10,
        min_similarity: float = 0.5,
        status_filter: list[str] | None = None,
    ) -> list[dict]:
        """Search playbook versions by semantic similarity."""
        query_embedding = await self.embedding_service.embed(query)
        embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"

        status_clause = ""
        params: dict = {
            "embedding": embedding_str,
            "min_similarity": min_similarity,
            "limit": limit,
        }
        if status_filter:
            status_clause = "AND pv.status = ANY(:statuses)"
            params["statuses"] = status_filter

        result = await db.execute(
            text(f"""
                SELECT
                    p.id AS playbook_id,
                    p.name,
                    p.description,
                    pv.id AS version_id,
                    pv.version_number,
                    pv.status,
                    pv.workflow_spec,
                    1 - (pv.embedding <=> CAST(:embedding AS vector)) AS similarity
                FROM playbook_versions pv
                JOIN playbooks p ON p.id = pv.playbook_id
                WHERE pv.embedding IS NOT NULL
                  AND 1 - (pv.embedding <=> CAST(:embedding AS vector)) >= :min_similarity
                  {status_clause}
                ORDER BY pv.embedding <=> CAST(:embedding AS vector)
                LIMIT :limit
            """),
            params,
        )
        return [dict(row._mapping) for row in result.fetchall()]
