"""Entrypoint: window-worker process.

Consumes normalized log stream, maintains rolling 5-minute windows,
and calls the classifier when windows close.

Usage:  cd backend && python main_window_worker.py
"""

import asyncio
import logging

from redis.asyncio import Redis

from app.config import get_settings
from app.services.classifier_client import ClassifierClient
from app.workers.window_worker import WindowWorker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


async def main() -> None:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    classifier = ClassifierClient()

    worker = WindowWorker(settings, redis, classifier)
    try:
        await worker.run()
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
