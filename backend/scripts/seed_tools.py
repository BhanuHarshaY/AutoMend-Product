"""Seeds the tool registry with default tools from §17.3.

Idempotent: skips tools that already exist (matched by name).

Usage:
    cd backend && python scripts/seed_tools.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure backend/ is on the path when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models.db import Base
from app.stores import postgres_store as store
from scripts.seed_data import DEFAULT_TOOLS


async def seed_tools() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.postgres_url)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        created = 0
        skipped = 0
        for tool_data in DEFAULT_TOOLS:
            existing = await store.get_tool_by_name(session, tool_data["name"])
            if existing is not None:
                print(f"  SKIP  {tool_data['name']} (already exists)")
                skipped += 1
                continue
            await store.create_tool(session, **tool_data)
            print(f"  ADD   {tool_data['name']}")
            created += 1
        await session.commit()

    await engine.dispose()
    print(f"\nDone: {created} created, {skipped} skipped (of {len(DEFAULT_TOOLS)} total)")


if __name__ == "__main__":
    print("Seeding default tools...")
    asyncio.run(seed_tools())
