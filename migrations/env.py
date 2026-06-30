"""
Alembic migration environment.

Reads DATABASE_URL from app config so migrations always target
the same database as the running application.
Supports async engines (asyncpg for PostgreSQL).
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.infra.database import Base

# Import all models so Alembic knows about every table.
# Without this import, Alembic can't detect tables to create/migrate.
from app.models import db as _models  # noqa: F401

# Alembic config object — provides access to alembic.ini values.
config = context.config

# Set up Python logging from alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata object that contains all table definitions.
# Alembic compares this against the actual DB to generate migrations.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode — generates SQL without
    connecting to the database. Useful for reviewing changes.
    """
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using an async engine (required for asyncpg)."""
    from app.infra.database import _build_engine_kwargs
    url, kwargs = _build_engine_kwargs(settings.database_url)
    engine = create_async_engine(url, **kwargs)
    async with engine.begin() as conn:
        await conn.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connects and migrates."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()