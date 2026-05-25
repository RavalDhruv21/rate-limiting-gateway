# Rate-Limited Public API Gateway

> A production-shaped API gateway implementing per-user rate limiting, JWT authentication, and request logging. Built with FastAPI, Redis, and PostgreSQL — following the same architectural pattern used by AWS API Gateway, Kong, and the gateway layer behind public APIs like Stripe and Twitter.

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com/)
[![Redis](https://img.shields.io/badge/Redis-7.0+-red.svg)](https://redis.io/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16+-blue.svg)](https://www.postgresql.org/)
[![Tests](https://img.shields.io/badge/tests-27%20passing-brightgreen.svg)](#testing)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## What This Is

A **middleware API gateway** that sits between clients and backend services,
enforcing per-user rate limits, validating JWT-based authentication, and
logging every request.

Key infrastructure:
- **Redis** — distributed, atomic token bucket rate limiting via Lua scripts
- **PostgreSQL** — durable request logging with SQL querying
- **FastAPI** — async-native web framework, ideal for I/O-bound gateway workloads
- **Docker Compose** — one command to start the full infrastructure

>The storage layer is built behind abstract interfaces (`RateLimiter`, `LogStore`),
making implementations swappable without touching service, middleware, or route code.
---

## Architecture

```
                    ┌─────────────────┐
                    │     Clients     │
                    └────────┬────────┘
                             │  HTTPS + JWT
                             ▼
            ┌────────────────────────────────────┐
            │       API Gateway (FastAPI)        │
            │                                    │
            │   1. Request ID                    │
            │   2. Logging (timer start)         │
            │   3. JWT Authentication            │
            │   4. Rate Limiter (token bucket) ──┼──► Redis
            │   5. Proxy / Forwarder             │
            │                                    │
            │   Logging (fire-and-forget) ───────┼──► PostgreSQL
            └────────────────┬───────────────────┘
                             ▼
                    ┌─────────────────┐
                    │ Backend Service │
                    └─────────────────┘
```

---

## Features

- **JWT authentication** with tier-aware claims (`free` / `pro` / `enterprise`)
- **Token bucket rate limiter** — allows bursts, refills continuously, atomic via Redis Lua in v2
- **Per-user quota overrides** — admins boost or restrict users at runtime without redeploying
- **Fire-and-forget request logging** — DB hiccups never slow user requests
- **Standardized error responses** — every error returns the same JSON envelope with request ID
- **Auto-generated API docs** at `/docs` (Swagger UI)
- **27 passing tests** — unit, concurrency, and full-stack integration
- **Pluggable storage** — swap Redis/PostgreSQL for in-memory/SQLite with one line
- **Alembic migrations** — proper schema management for PostgreSQL
- **Docker Compose** — one command to start the full infrastructure

---

## How to Run

### Prerequisites

- **Python 3.12+** — https://www.python.org/downloads/
- **Git** — https://git-scm.com/
- **Docker Desktop** — https://www.docker.com/products/docker-desktop/

---

> **Before starting the server:** open `.env` and set `JWT_SECRET` and `ADMIN_API_KEY` to random strings.
> Generate them with: `python -c "import secrets; print(secrets.token_urlsafe(32))"`

---

### ▶ v2 — Redis + PostgreSQL (Docker Required)

```powershell
git clone https://github.com/YOUR_USERNAME/rate-limited-gateway.git
cd rate-limited-gateway
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
docker compose up -d
python -m alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

> **Before starting:** open `.env`, set `JWT_SECRET` and `ADMIN_API_KEY` to random strings,
> and update the URLs to match your Docker ports:
> ```
> DATABASE_URL="postgresql+asyncpg://gateway_user:gateway_pass@localhost:5432/gateway_db"
> REDIS_URL="redis://localhost:6379/0"
> ```
> Start Docker Desktop first and wait for it to fully load before running `docker compose up -d`.

Server starts with:
```
INFO: Gateway ready. Redis + PostgreSQL active.
INFO: Application startup complete.
```

Open `http://localhost:8000/docs` in your browser to explore the API interactively.

---

## Example Usage

In a second terminal (with venv activated):

```powershell
# Mint a JWT token
$token = python scripts/generate_token.py alice free
$headers = @{ Authorization = "Bearer $token" }

# Authenticated request — proxied to httpbin.org
Invoke-RestMethod -Uri http://localhost:8000/get -Headers $headers

# Set a rate limit override for alice (admin only)
$adminKey = (Get-Content .env | Select-String "^ADMIN_API_KEY=").ToString().Split("=",2)[1].Trim('"')
$adminHeaders = @{ "X-Admin-Key" = $adminKey }
Invoke-RestMethod -Uri http://localhost:8000/admin/quota/alice -Method PUT `
  -Headers $adminHeaders `
  -Body '{"custom_limit": 3, "reason": "demo"}' `
  -ContentType "application/json"

# Trigger rate limiting — first 3 succeed, rest get 429
1..6 | ForEach-Object {
    try {
        Invoke-WebRequest -Uri http://localhost:8000/get -Headers $headers -UseBasicParsing -ErrorAction Stop | Out-Null
        "Request $_`: 200 OK"
    } catch {
        "Request $_`: $($_.Exception.Response.StatusCode.value__) RATE LIMITED"
    }
}

# View stats
Invoke-RestMethod -Uri http://localhost:8000/admin/stats -Headers $adminHeaders
```

---

## API Reference

### Public

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness probe. Returns `{"status": "ok"}`. |
| POST | `/auth/token` | Mint a JWT (dev-only). Body: `{"user_id": "...", "tier": "free"}`. |

### Authenticated (Requires `Authorization: Bearer <jwt>`)

| Method | Path | Description |
|---|---|---|
| ANY | `/{path}` | Catch-all proxy. Forwards to `UPSTREAM_BASE_URL`. |

### Admin (Requires `X-Admin-Key: <key>`)

| Method | Path | Description |
|---|---|---|
| GET | `/admin/quota/{user_id}` | View quota override. |
| PUT | `/admin/quota/{user_id}` | Set or update quota override. |
| DELETE | `/admin/quota/{user_id}` | Remove override (revert to tier default). |
| GET | `/admin/logs` | Recent request logs. Params: `user_id`, `limit`. |
| GET | `/admin/stats` | Aggregate metrics (last 24h). Param: `user_id`. |

### Response Headers

| Header | Meaning |
|---|---|
| `X-Request-ID` | Unique request ID, propagated to logs. |
| `X-RateLimit-Limit` | Applicable rate limit. |
| `X-RateLimit-Remaining` | Tokens remaining in bucket. |
| `Retry-After` | (429 only) Seconds until retry. |

### Standardized Error Shape

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

Error codes: `UNAUTHORIZED`, `INVALID_TOKEN`, `TOKEN_EXPIRED`, `FORBIDDEN`,
`RATE_LIMITED`, `UPSTREAM_ERROR`, `UPSTREAM_TIMEOUT`, `NOT_FOUND`, `INTERNAL_ERROR`.

---

## Project Structure

```
rate-limited-gateway/
├── app/
│   ├── main.py                  # FastAPI factory, middleware order, lifespan
│   ├── dependencies.py          # DI seam — THE file that changed v1 → v2
│   ├── core/
│   │   ├── config.py            # Typed settings from .env
│   │   ├── security.py          # JWT encode/decode
│   │   └── errors.py            # Exception hierarchy + error shape
│   ├── models/
│   │   ├── db.py                # SQLAlchemy ORM tables
│   │   └── schemas.py           # Pydantic API contracts
│   ├── infra/                   # ← PLUGGABLE RING
│   │   ├── database.py          # Async engine + session factory
│   │   ├── rate_limiter/
│   │   │   ├── base.py          # RateLimiter abstract interface
│   │   │   ├── algorithms.py    # Pure token-bucket math (reused in both versions)
│   │   │   ├── memory.py        # v1: in-memory implementation
│   │   │   └── redis_limiter.py # v2: Redis + Lua atomic implementation
│   │   └── log_store/
│   │       ├── base.py          # LogStore abstract interface
│   │       └── sqlite.py        # Works for both SQLite and PostgreSQL
│   ├── services/                # Business logic — unchanged between v1 and v2
│   ├── middleware/              # HTTP interceptors — unchanged between v1 and v2
│   ├── routes/                  # Endpoints — unchanged between v1 and v2
│   └── utils/
├── migrations/                  # Alembic schema migrations (v2)
├── tests/                       # 27 tests: unit, concurrency, integration
├── scripts/
│   ├── init_db.py               # CLI: initialize database
│   └── generate_token.py        # CLI: mint a JWT for testing
├── docker-compose.yml           # Redis + PostgreSQL services
├── .env.example                 # Environment variable template
└── pyproject.toml               # Dependencies + tool config
```

---

## Testing

```powershell
pytest -v
pytest --cov=app --cov-report=term-missing
```

Tests use SQLite in-memory and `InMemoryRateLimiter` — completely isolated from Redis/PostgreSQL. All 27 tests pass regardless of whether Docker is running.

Three levels covered:

- **Unit** — token bucket math, JWT primitives. Milliseconds per test.
- **Concurrency** — 20 parallel requests against a limit of 5, verifies exactly 5 allowed. Catches race conditions.
- **Integration** — full HTTP stack via httpx ASGI transport. Verifies middleware ordering, error shapes, and the 429-not-500 regression.

---

## Future Work

- Sliding window algorithm alongside token bucket, selectable per endpoint
- Per-endpoint rate limits with different quotas per upstream path
- API key authentication as alternative to JWT
- Prometheus `/metrics` endpoint for observability
- Circuit breaker around upstream proxy
- Response caching for idempotent GETs
- Natural language admin interface via LLM integration

---

## License

MIT — see [LICENSE](LICENSE).
