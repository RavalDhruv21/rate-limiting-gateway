"""
Async SQLAlchemy engine, session factory, and ORM base class.

This module is the one place that knows about the database driver.
Every other file talks to SQLAlchemy through these abstractions —
which is why swapping SQLite for Postgres in v2 is a config change,
not a code change.

Three exported objects:
  - `engine`:    the long-lived async engine (connection pool).
  - `AsyncSessionLocal`: factory that yields a fresh AsyncSession per call.
  - `Base`:      parent class for all ORM models in app/models/db.py.

Plus two helpers:
  - `get_db_session`: a dependency for FastAPI routes that need a session.
  - `init_db`:        creates all tables on a fresh database.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


# ─── The engine ────────────────────────────────────────────
# `echo=False` keeps SQL out of the logs in development.
# Flip to True temporarily when debugging weird queries.
#
# `future=True` is the default in SQLAlchemy 2.x — included for
# clarity to future readers.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
)


# ─── The session factory ───────────────────────────────────
# Produces fresh AsyncSession objects on demand.
#
# `expire_on_commit=False` means after we commit, ORM objects
# remain usable (otherwise SQLAlchemy expires their attributes
# and accessing them triggers a lazy-load — which fails in async
# code outside a session). This is the FastAPI-recommended setting.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# ─── ORM base class ────────────────────────────────────────
# All models in app/models/db.py inherit from this.
# SQLAlchemy uses Base.metadata as the registry of every table
# the app knows about — that's how init_db() finds them all.
class Base(DeclarativeBase):
    """Common base for all ORM models."""
    pass


# ─── Helpers ───────────────────────────────────────────────
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields a session per request.

    Usage in a route:
        async def my_route(db: AsyncSession = Depends(get_db_session)):
            ...

    The session is closed automatically when the request ends, even
    on exception. We don't commit here — the caller is responsible
    for committing if they made changes. (This is intentional: a
    middleware that only reads shouldn't accidentally commit anything.)
    """
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """
    Create all tables defined in app/models/db.py.

    Called from main.py at startup, and from scripts/init_db.py for
    a one-shot setup. Safe to call repeatedly — it only creates
    tables that don't already exist.

    In production with Postgres we'd use Alembic migrations instead;
    create_all is fine for a v1 SQLite project.
    """
    # The import is inside the function to avoid a circular import:
    # models/db.py imports Base from this file. If we import it at
    # module top here, Python sees the cycle. By the time init_db()
    # is called, all modules are loaded and the import succeeds.
    from app.models import db as _db_models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)