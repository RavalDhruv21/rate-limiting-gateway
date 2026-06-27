"""
Pytest fixtures for the test suite.

Tests require running PostgreSQL and Redis instances (docker compose up -d).
Each test gets a clean slate:
  - Postgres: all tables are created before the test and dropped after.
  - Redis: the DB is flushed before each test via the test_redis fixture.
The httpx client talks to the FastAPI app directly via ASGI transport —
no actual network sockets, no port conflicts.
"""

from collections.abc import AsyncGenerator
from typing import Literal

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import dependencies
from app.core.config import settings
from app.core.security import create_access_token
from app.infra.database import Base
from app.infra.log_store.postgres_log_store import PostgresLogStore
from app.infra.rate_limiter.redis_limiter import RedisRateLimiter
from app.main import app as fastapi_app

# ─── Database fixture ──────────────────────────────────────

@pytest_asyncio.fixture(scope="function")
async def test_engine():
    """
    Create all tables in the configured Postgres database before each test
    and drop them after. scope="function" keeps tests fully independent.
    """
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def test_session_factory(test_engine):
    """Session factory bound to the test engine."""
    return async_sessionmaker(
        bind=test_engine,
        expire_on_commit=False,
        autoflush=False,
    )


# ─── Redis fixture ─────────────────────────────────────────

@pytest_asyncio.fixture(scope="function")
async def test_redis():
    """
    Redis client for tests. Flushes the entire DB before each test
    so rate-limit buckets from previous tests don't bleed through.
    """
    client = aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=False,
    )
    await client.flushdb()
    yield client
    await client.aclose()  # type: ignore[attr-defined]


# ─── App fixture with overridden dependencies + lifespan ──

@pytest_asyncio.fixture(scope="function")
async def app(test_engine, test_session_factory, test_redis, monkeypatch):
    """
    The FastAPI app with test-friendly dependencies wired in.

    Swaps:
      - engine / AsyncSessionLocal → test_engine / test_session_factory
        (all DB writes go to the test schema, dropped after the test)
      - _rate_limiter → fresh RedisRateLimiter on the flushed test Redis DB
      - _log_store    → fresh PostgresLogStore (writes go to the test schema)

    The lifespan is manually entered so app.state.http_client is created —
    ASGITransport does not run lifespan automatically.
    """
    import app.infra.database as db_module

    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", test_session_factory)

    fresh_limiter = RedisRateLimiter(test_redis)
    monkeypatch.setattr(dependencies, "_rate_limiter", fresh_limiter)

    fresh_store = PostgresLogStore()
    monkeypatch.setattr(dependencies, "_log_store", fresh_store)

    async with fastapi_app.router.lifespan_context(fastapi_app):
        yield fastapi_app


@pytest_asyncio.fixture(scope="function")
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    """httpx AsyncClient pointed at the FastAPI app via ASGI transport."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


# ─── Token fixtures for authenticated tests ───────────────

Tier = Literal["free", "pro", "enterprise"]


def _make_token(user_id: str = "test_user", tier: Tier = "free") -> str:
    """Synchronous helper for minting tokens in tests."""
    return create_access_token(user_id=user_id, tier=tier)


@pytest.fixture
def auth_headers():
    """Authorization headers for a default free-tier user."""
    token = _make_token("test_user", "free")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers():
    """Headers for hitting admin endpoints."""
    return {"X-Admin-Key": settings.admin_api_key}
