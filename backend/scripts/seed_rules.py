"""Seeds default alert rules matching the Prometheus alert rules in infra config.

Idempotent: skips rules that already exist (matched by name).

Usage:
    cd backend && python scripts/seed_rules.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models.db import AlertRule
from app.stores import postgres_store as store
from scripts.seed_data import DEFAULT_ALERT_RULES


async def seed_rules() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.postgres_url)

    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        created = 0
        skipped = 0
        for rule_data in DEFAULT_ALERT_RULES:
            # Check if rule with this name already exists
            result = await session.execute(
                select(AlertRule).where(AlertRule.name == rule_data["name"])
            )
            if result.scalar_one_or_none() is not None:
                print(f"  SKIP  {rule_data['name']} (already exists)")
                skipped += 1
                continue
            await store.create_alert_rule(session, **rule_data)
            print(f"  ADD   {rule_data['name']}")
            created += 1
        await session.commit()

    await engine.dispose()
    print(f"\nDone: {created} created, {skipped} skipped (of {len(DEFAULT_ALERT_RULES)} total)")


if __name__ == "__main__":
    print("Seeding default alert rules...")
    asyncio.run(seed_rules())
