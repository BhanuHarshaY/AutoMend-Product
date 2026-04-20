"""Shared test fixtures for the AutoMend backend test suite."""

import pytest

from app.config import Settings


@pytest.fixture()
def settings() -> Settings:
    """Return a Settings instance with test defaults (no .env file needed)."""
    return Settings(
        app_env="test",
        debug=True,
        postgres_host="localhost",
        redis_host="localhost",
    )
