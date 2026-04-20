"""Entrypoint: correlation-worker process.

Consumes classifier outputs and Alertmanager webhooks, correlates signals
into incidents, and starts remediation workflows via Temporal.

Usage:  cd backend && python main_correlation_worker.py
"""

import asyncio
import logging

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from temporalio.client import Client as TemporalClient

from app.config import get_settings
from app.workers.correlation_worker import CorrelationWorker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


async def main() -> None:
    settings = get_settings()

    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    engine = create_async_engine(settings.postgres_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    temporal = None
    try:
        temporal = await TemporalClient.connect(
            settings.temporal_server_url,
            namespace=settings.temporal_namespace,
            lazy=True,
        )
    except Exception:
        logging.warning("Temporal not available — workflows will not start")

    worker = CorrelationWorker(settings, redis, session_factory, temporal)
    try:
        await worker.run()
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
