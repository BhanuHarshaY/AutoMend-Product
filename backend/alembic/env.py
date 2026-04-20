"""Alembic environment configuration.

Supports both offline (SQL generation) and online (direct DB) migrations.
Reads the database URL from app.config.Settings so the single source of
truth for connection details is the AUTOMEND_* environment variables.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings

# Alembic Config object — provides access to alembic.ini values.
config = context.config

# Override sqlalchemy.url with the value from our Settings.
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.postgres_url_sync)

# Set up Python logging from alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import the declarative Base metadata so autogenerate can detect changes.
from app.models.db import Base  # noqa: E402

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL scripts without connecting to the database."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
