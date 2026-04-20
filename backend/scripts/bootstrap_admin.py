"""Create (or update) an admin user. Idempotent.

Usage (from inside an API pod):
    kubectl exec -n automend deployment/automend-api -- \
        env ADMIN_EMAIL=admin@local ADMIN_PASSWORD=admin123 \
        python scripts/bootstrap_admin.py

The script reads ``ADMIN_EMAIL`` and ``ADMIN_PASSWORD`` from the environment
so credentials never land in a pod's command line. It connects to Postgres
using the same ``AUTOMEND_POSTGRES_*`` env vars the app itself uses.

If the user already exists, its password is left alone and the role is
ensured to be ``admin`` (safe to re-run).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Dockerfile.api installs the project via `pip install .` before copying the
# source, so the `app` package isn't registered as a module. uvicorn works
# because it prepends CWD to sys.path at runtime; plain scripts don't get
# that treatment. Add /app (this file's grandparent) to sys.path so the
# script runs cleanly via `python scripts/bootstrap_admin.py` without the
# caller having to set PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from passlib.context import CryptContext  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.stores import postgres_store as pg  # noqa: E402


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        print(f"ERROR: {name} env var is required", file=sys.stderr)
        sys.exit(2)
    return value


async def _main() -> int:
    email = _require_env("ADMIN_EMAIL")
    password = _require_env("ADMIN_PASSWORD")

    settings = get_settings()
    pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

    engine = create_async_engine(settings.postgres_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            existing = await pg.get_user_by_email(session, email)
            if existing is not None:
                if existing.role != "admin":
                    existing.role = "admin"
                    await session.commit()
                    print(f"User {email} already existed — promoted to admin.")
                else:
                    print(f"User {email} already exists as admin — no change.")
                return 0

            await pg.create_user(
                session,
                email=email,
                display_name="Admin",
                role="admin",
                hashed_password=pwd.hash(password),
                is_active=True,
            )
            await session.commit()
            print(f"Created admin user {email}.")
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
