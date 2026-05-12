# Rate-Limited Public API Gateway

> A production-shaped API gateway implementing per-user rate limiting, JWT authentication, and request logging. Built with FastAPI, in-memory state, and SQLite — designed to upgrade cleanly to Redis and PostgreSQL.

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/tests-27%20passing-brightgreen.svg)](#testing)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## What This Is

A **middleware API gateway** that sits between clients and backend services, enforcing per-user rate limits, validating JWT-based authentication, and logging every request. This is the same architectural pattern used by AWS API Gateway, Kong, and the gateway layer behind public APIs like Stripe and Twitter.

This implementation is intentionally simplified — it uses in-memory state for rate limiting and SQLite for request logging — but every component is built behind an **abstract interface**. Swapping the in-memory rate limiter for Redis, or SQLite for PostgreSQL, is a single-line dependency change. The architecture is the production architecture; the storage backends are the learning-friendly ones.

## Architecture

```
                    ┌─────────────────┐
                    │     Clients     │
                    │  (web, mobile,  │
                    │   partners)     │
                    └────────┬────────┘
                             │  HTTPS + JWT
                             ▼
            ┌────────────────────────────────────┐
            │       API Gateway (FastAPI)        │
            │                                    │
            │   ┌──────────────────────────┐    │
            │   │ 1. Request ID            │    │
            │   ├──────────────────────────┤    │
            │   │ 2. Logging (timer start) │    │
            │   ├──────────────────────────┤    │
            │   │ 3. JWT Authentication    │    │
            │   ├──────────────────────────┤    │
            │   │ 4. Rate Limiter          │◄───┼─── State store
            │   │    (token bucket)        │    │   (in-memory now;
            │   ├──────────────────────────┤    │    Redis in v2)
            │   │ 5. Proxy / Forwarder     │    │
            │   └──────────────────────────┘    │
            │                                    │
            │   Logging (timer stop) ───────────────► SQLite
            │                                       (Postgres in v2)
            └────────────────┬───────────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │ Backend Service │
                    │  (any API the   │
                    │ gateway fronts) │
                    └─────────────────┘
```

The architecture follows a **layered design with pluggable storage interfaces**. The middleware chain handles every cross-cutting concern (auth, rate limiting, logging) so backend services behind the gateway only worry about business logic. Storage is abstracted behind `RateLimiter` and `LogStore` interfaces, defined in `app/infra/`.

## Features

- **JWT authentication middleware** with tier-aware claims (`free` / `pro` / `enterprise`).
- **Token bucket rate limiter** — allows bursts up to the configured limit, refills continuously. Race-condition safe via per-key async locks.
- **Per-user quota overrides** — admins can boost or restrict specific users without redeploying.
- **Fire-and-forget request logging** — database hiccups never slow user requests.
- **Standardized error responses** — every error returns the same JSON envelope with codes, messages, and request IDs.
- **Auto-generated OpenAPI docs** at `/docs`.
- **Comprehensive test suite** — 27 tests covering unit, concurrency, and full-stack integration.
- **Pluggable storage** — `RateLimiter` and `LogStore` are abstract interfaces. Swap implementations without touching application code.

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Web framework | FastAPI + Uvicorn | Async-native, type-driven, ideal for I/O-bound gateways |
| Rate limit storage | Python dicts + `asyncio.Lock` | Simple, atomic per-key; swap to Redis for distributed deployments |
| Log storage | SQLite via SQLAlchemy 2.0 (async) | File-based for v1; swap to PostgreSQL by changing the URL |
| Auth | python-jose (HS256 JWT) | Self-contained tokens; no DB hit per request |
| Config | pydantic-settings | Typed `.env` loading with fail-fast validation |
| HTTP client | httpx (async) | Forwards requests to the upstream backend |
| Testing | pytest + pytest-asyncio + httpx ASGI transport | In-process integration tests, no real network |

## Quick Start

### Prerequisites

- Python 3.12+
- Windows, macOS, or Linux (Windows-native works without WSL)

### Setup

```powershell
# Clone and enter the project
git clone https://github.com/RavalDhruv21/rate-limited-gateway.git
cd rate-limited-gateway

# Create and activate the virtual environment
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1     # Windows
# source .venv/bin/activate      # macOS / Linux

# Install dependencies (including dev tools for tests)
pip install -e ".[dev]"

# Set up environment variables
copy .env.example .env           # Windows
# cp .env.example .env           # macOS / Linux

# Generate real JWT and admin secrets and paste them into .env:
python -c "import secrets; print(secrets.token_urlsafe(32))"
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Open `.env` and replace `JWT_SECRET` and `ADMIN_API_KEY` with the two values you just generated.

### Initialize the Database

```powershell
python scripts/init_db.py
```

### Run the Server

```powershell
uvicorn app.main:app --reload --port 8000
```

The server is now running at `http://localhost:8000`. The auto-generated OpenAPI UI is at `http://localhost:8000/docs`.

## Example Usage

In a second terminal (with the venv activated):

### Mint a JWT

```powershell
$token = python scripts/generate_token.py alice free
$headers = @{ Authorization = "Bearer $token" }
```

### Make an Authenticated Request

```powershell
Invoke-RestMethod -Uri http://localhost:8000/get -Headers $headers
```

The gateway forwards the request to the configured upstream (default: `httpbin.org`) and returns the response.

### Inspect Rate-Limit Headers

```powershell
$r = Invoke-WebRequest -Uri http://localhost:8000/get -Headers $headers
$r.Headers["X-RateLimit-Limit"]
$r.Headers["X-RateLimit-Remaining"]
```

### Trigger a 429

```powershell
# Set a tight limit for alice via the admin endpoint
$adminKey = (Get-Content .env | Select-String "^ADMIN_API_KEY=").ToString().Split("=", 2)[1].Trim('"')
$adminHeaders = @{ "X-Admin-Key" = $adminKey }
$body = @{ custom_limit = 3; reason = "demo" } | ConvertTo-Json
Invoke-RestMethod -Uri http://localhost:8000/admin/quota/alice -Method PUT -Headers $adminHeaders -Body $body -ContentType "application/json"

# Burn through the limit
1..6 | ForEach-Object {
    try {
        Invoke-WebRequest -Uri http://localhost:8000/get -Headers $headers -ErrorAction Stop | Out-Null
        "Request $_`: 200 OK"
    } catch {
        "Request $_`: $($_.Exception.Response.StatusCode.value__) RATE LIMITED"
    }
}
```

You'll see three `200 OK` responses, then `429 RATE LIMITED` for the rest.

### View Logs and Stats

```powershell
Invoke-RestMethod -Uri http://localhost:8000/admin/stats -Headers $adminHeaders
Invoke-RestMethod -Uri "http://localhost:8000/admin/logs?user_id=alice&limit=10" -Headers $adminHeaders
```

## API Reference

### Public

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness probe. Returns `{"status": "ok"}`. |
| POST | `/auth/token` | Mint a JWT (dev-only). Body: `{"user_id": "...", "tier": "free"}`. |

### Authenticated (Requires `Authorization: Bearer <jwt>`)

| Method | Path | Description |
|---|---|---|
| ANY | `/{path}` | Catch-all proxy. Forwards to `UPSTREAM_BASE_URL` after auth and rate-limit checks. |

### Admin (Requires `X-Admin-Key: <key>`)

| Method | Path | Description |
|---|---|---|
| GET | `/admin/quota/{user_id}` | View a user's quota override (404 if none). |
| PUT | `/admin/quota/{user_id}` | Set or update a user's quota override. |
| DELETE | `/admin/quota/{user_id}` | Remove a user's override (revert to tier default). |
| GET | `/admin/logs` | Recent request logs. Query params: `user_id`, `limit`. |
| GET | `/admin/stats` | Aggregate metrics over the last 24 hours. Query param: `user_id`. |

### Response Headers (Authenticated Requests)

| Header | Meaning |
|---|---|
| `X-Request-ID` | Unique ID for this request, propagated to logs. |
| `X-RateLimit-Limit` | The applicable rate limit. |
| `X-RateLimit-Remaining` | Tokens remaining in the user's bucket. |
| `Retry-After` | (On 429 only) Seconds until the user should retry. |

### Standardized Error Response

Every error returns this shape:

```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "Rate limit exceeded. Try again in 23 seconds.",
    "details": { "retry_after": 23 }
  },
  "request_id": "req_8f3a2b1c..."
}
```

Codes: `UNAUTHORIZED`, `INVALID_TOKEN`, `TOKEN_EXPIRED`, `FORBIDDEN`, `RATE_LIMITED`, `UPSTREAM_ERROR`, `UPSTREAM_TIMEOUT`, `NOT_FOUND`, `INTERNAL_ERROR`.

## Project Structure

```
rate-limited-gateway/
├── app/
│   ├── main.py                  # FastAPI factory, middleware order, lifespan
│   ├── dependencies.py          # DI seam — picks the implementation to use
│   ├── core/
│   │   ├── config.py            # Typed settings from .env
│   │   ├── security.py          # JWT encode/decode primitives
│   │   └── errors.py            # Custom exception hierarchy + error shape
│   ├── models/
│   │   ├── db.py                # SQLAlchemy ORM tables
│   │   └── schemas.py           # Pydantic API contracts
│   ├── infra/                   # ← PLUGGABLE RING
│   │   ├── database.py          # Async engine + session factory
│   │   ├── rate_limiter/
│   │   │   ├── base.py          # RateLimiter abstract interface
│   │   │   ├── algorithms.py    # Pure token-bucket math
│   │   │   └── memory.py        # In-memory implementation
│   │   └── log_store/
│   │       ├── base.py          # LogStore abstract interface
│   │       └── sqlite.py        # SQLite/SQLAlchemy implementation
│   ├── services/                # Business logic (uses infra)
│   ├── middleware/              # FastAPI middleware (uses services)
│   ├── routes/                  # HTTP endpoints
│   └── utils/                   # Pure helpers
├── tests/                       # 27 tests: unit, concurrency, integration
├── scripts/
│   ├── init_db.py               # CLI: create tables
│   └── generate_token.py        # CLI: mint a JWT
├── .env.example                 # Template for required env vars
├── pyproject.toml               # Dependencies, ruff/pytest config
└── README.md
```

## Testing

```powershell
# Run all 27 tests
pytest -v

# Run with coverage report
pytest --cov=app --cov-report=term-missing
```

The suite covers three levels:

1. **Unit tests** (`test_security.py`, `test_rate_limit.py::TestTokenBucketAlgorithm`) — pure logic, milliseconds per test. Catches math bugs in the rate limiter and JWT primitives.
2. **Concurrency tests** (`test_rate_limit.py::TestInMemoryRateLimiter::test_concurrent_requests_atomic`) — fires 20 concurrent requests against a limit of 5 and verifies exactly 5 are allowed. Catches the read-modify-write race condition that humans can't trigger by clicking through curl.
3. **Integration tests** (`test_integration.py`) — full HTTP stack via httpx's ASGI transport. Verifies middleware ordering, exception handling, status codes, headers, and end-to-end behavior. One test specifically regression-checks "rate-limit-exceeded returns 429, not 500" — a real bug caught during development.

## Design Decisions

### Why token bucket over fixed-window counters?

A naive fixed-window counter ("60 requests per clock minute") suffers from a boundary problem: a user can send 60 requests at 12:00:59 and 60 more at 12:01:00, hitting your backend with 120 requests in one second. Token bucket avoids this by tracking continuous refill — a user's bucket accumulates tokens at the configured rate, smoothing the impact of bursts and matching the natural shape of real client traffic.

### Why abstract interfaces around storage?

The interesting architectural work in this project is `app/infra/`, where `RateLimiter` and `LogStore` are abstract base classes with one v1 implementation each. The service layer depends on the abstract type, never the concrete class. In v2 we add `RedisRateLimiter` and `PostgresLogStore` as new files; the dependency-injection seam (`app/dependencies.py`) picks one. **No service, middleware, or route code changes.** This is what "designed to scale" actually looks like at the code level.

### Why fire-and-forget logging?

If the logging middleware awaited every database write before responding, gateway latency would be bounded by database latency — a Postgres hiccup would cascade into client timeouts. By scheduling log writes as background tasks (`asyncio.create_task`), the gateway's response time is independent of log-store performance. Tradeoff: under heavy stress or crashes, log entries can be lost. In a production system you'd put a buffered queue (Kafka, SQS) between the gateway and the database to reduce loss without re-introducing blocking. For v1 the simplification is honest.

### Why JWT instead of session-based auth?

A session scheme requires a database lookup on every request to translate session ID → user. JWTs are self-contained: signature validation is cryptographic (no DB), and we embed the user's tier directly in the payload. Cost: tier changes don't take effect until the user's current JWT expires (15 min – 1 hour). That's an acceptable tradeoff for ~100x better throughput.

### Why separate `models/db.py` from `models/schemas.py`?

DB models describe storage; API schemas describe the HTTP contract. Conflating them leaks DB internals to clients, and forces API versioning every time you rename a column. The split keeps storage and API independently evolvable — a discipline worth maintaining from day one, even though it costs a few extra files.

### Why admin auth is separate from user JWT auth

Admin credentials have a different trust domain, different rotation cadence, and different blast radius on compromise. Mixing them into the same JWT scheme is a classic security mistake. The admin plane uses a static `X-Admin-Key` header, validated via a FastAPI dependency on the entire `/admin/*` router.

## Upgrade Path (v2)

The project is designed so the in-memory rate limiter and SQLite log store can be replaced with Redis and PostgreSQL **with no changes to service, middleware, or route code**.

### Replace in-memory rate limiter → Redis

1. Add `redis>=5.0` to `pyproject.toml`.
2. Create `app/infra/rate_limiter/redis_limiter.py` implementing the `RateLimiter` abstract interface. The token-bucket math from `algorithms.py` is reused inside a Lua script for atomicity.
3. In `app/dependencies.py`, change one line:

```python
# Before:
_rate_limiter: RateLimiter = InMemoryRateLimiter()

# After:
_rate_limiter: RateLimiter = RedisRateLimiter(redis_client)
```

Every other file is untouched.

### Replace SQLite → PostgreSQL

1. Add `asyncpg>=0.29` to `pyproject.toml`.
2. In `.env`, change `DATABASE_URL`:

```bash
# Before:
DATABASE_URL="sqlite+aiosqlite:///./gateway.db"

# After:
DATABASE_URL="postgresql+asyncpg://user:pass@host:5432/dbname"
```

3. Replace `init_db()` calls with Alembic migrations (production hygiene).

No application code changes — the database swap is purely configuration because SQLAlchemy is already database-agnostic.

## Future Work

- **Sliding window rate-limit algorithm** alongside token bucket, selectable per endpoint.
- **Per-endpoint rate limits** (different quotas for different upstream paths).
- **API key authentication** as an alternative to JWT.
- **Prometheus `/metrics` endpoint** for ops observability.
- **Circuit breaker** around the upstream proxy to fail fast on a struggling backend.
- **Response caching layer** for idempotent GETs.

## License

MIT — see [LICENSE](LICENSE).