"""
Pytest fixtures for the test suite.

Anything defined here is available to every test in tests/ via argument
injection. For example, a test that takes `client` as a parameter gets
the httpx AsyncClient fixture defined below.

Setup philosophy:
  - Tests use an in-memory SQLite database (separate from gateway.db),
    so the dev database is never touched.
  - The rate limiter is reset between tests so they don't interfere.
  - The FastAPI lifespan is manually triggered so app.state.http_client
    gets set up — httpx.ASGITransport doesn't run lifespan automatically.
  - The httpx client talks to the FastAPI app directly via ASGI
    transport — no actual network sockets, no port conflicts, fast.
"""

from collections.abc import AsyncGenerator
from typing import Literal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import dependencies
from app.core.config import settings
from app.core.security import create_access_token
from app.infra.database import Base
from app.infra.log_store.sqlite import SqliteLogStore
from app.infra.rate_limiter.memory import InMemoryRateLimiter
from app.main import app as fastapi_app

# ─── Database fixture ──────────────────────────────────────
# In-memory SQLite with a shared cache so multiple connections see the
# same data. The shared-cache trick is needed because in-memory SQLite
# is otherwise per-connection.
TEST_DATABASE_URL = "sqlite+aiosqlite:///file::memory:?cache=shared&uri=true"


@pytest_asyncio.fixture(scope="function")
async def test_engine():
    """
    Create a fresh in-memory database for each test.

    scope="function" means a brand-new database per test — clean slate
    every time. Slightly slower than session-scoped, but tests stay
    independent (a flaky test can't poison the rest of the suite).
    """
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
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


# ─── App fixture with overridden dependencies + lifespan ──

@pytest_asyncio.fixture(scope="function")
async def app(test_engine, test_session_factory, monkeypatch):
    """
    The FastAPI app with test-friendly dependencies wired in.

    We swap:
      - The global engine in app.infra.database → test_engine.
      - The session factory in app.infra.database → test_session_factory.
      - The rate limiter singleton → a fresh InMemoryRateLimiter
        (so each test starts with empty buckets).
      - The log store singleton → a fresh SqliteLogStore (so each test
        starts with an empty log table — and writes go to the in-memory
        DB, not gateway.db).

    Then we MANUALLY run the FastAPI lifespan so app.state.http_client
    gets created. httpx.ASGITransport doesn't run lifespan automatically.
    """
    import app.infra.database as db_module

    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", test_session_factory)

    # Reset the rate limiter — each test gets clean buckets.
    fresh_limiter = InMemoryRateLimiter()
    monkeypatch.setattr(dependencies, "_rate_limiter", fresh_limiter)

    # Reset the log store. Note: SqliteLogStore reads AsyncSessionLocal
    # at call time, so our monkeypatch above already redirects its writes.
    fresh_store = SqliteLogStore()
    monkeypatch.setattr(dependencies, "_log_store", fresh_store)

    # Manually enter the app's lifespan context.
    # This is what creates app.state.http_client and runs init_db().
    # The `async with` triggers @asynccontextmanager startup; the yield
    # at the end triggers shutdown.
    async with fastapi_app.router.lifespan_context(fastapi_app):
        yield fastapi_app


@pytest_asyncio.fixture(scope="function")
async def client(app) -> AsyncGenerator[AsyncClient, None]:
    """
    httpx AsyncClient pointed at the FastAPI app via ASGI transport.

    No real network — ASGITransport invokes the app in-process. Fast
    and avoids port conflicts when tests run in parallel.
    """
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
    """
    Authorization headers for a default free-tier user.

    Usage in a test:
        async def test_something(client, auth_headers):
            r = await client.get("/some/path", headers=auth_headers)
    """
    token = _make_token("test_user", "free")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers():
    """Headers for hitting admin endpoints."""
    return {"X-Admin-Key": settings.admin_api_key}