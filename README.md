# Rate-Limited Public API Gateway

> A production-shaped API gateway implementing per-user rate limiting, JWT authentication, and request logging. Built with FastAPI, Redis, and PostgreSQL — following the same architectural pattern used by AWS API Gateway, Kong, and the gateway layer behind public APIs like Stripe and Twitter.

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com/)
[![Redis](https://img.shields.io/badge/Redis-7.0+-red.svg)](https://redis.io/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16+-blue.svg)](https://www.postgresql.org/)
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
- **Docker Compose** — one command to start local infrastructure

> The storage layer is built behind abstract interfaces (`RateLimiter`, `LogStore`),
> making implementations swappable without touching service, middleware, or route code.

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
            │   1. Security Headers / CORS       │
            │   2. Request ID                    │
            │   3. Logging (timer start)         │
            │   4. JWT Authentication            │
            │   5. Rate Limiter (token bucket) ──┼──► Redis
            │   6. Circuit Breaker               │
            │   7. Proxy / Forwarder             │
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
- **Token bucket rate limiter** — allows bursts, refills continuously, atomic via Redis Lua scripts
- **Adaptive rate limiting** — automatically halves limits when upstream error rate > 20%
- **Circuit breaker** — opens after 5 upstream failures, fast-fails for 60s, probes on recovery
- **Redis fail-open** — if Redis is unreachable, requests are allowed rather than returning 500
- **Prometheus `/metrics`** — request rate, latency histograms, per-tier rate-limit counters
- **Pre-built Grafana dashboard** — importable `grafana/dashboard.json` for zero-config observability
- **Per-user quota overrides** — admins boost or restrict users at runtime without redeploying
- **Fire-and-forget request logging** — DB hiccups never slow user requests
- **Standardized error responses** — every error returns the same JSON envelope with request ID
- **IETF `X-RateLimit-Policy` header** — emerging standard rate limit header
- **`/ready` readiness probe** — checks Redis + Postgres before accepting traffic
- **Structured JSON logs** — parseable by Render / Grafana / ELK
- **Alembic migrations** — proper schema management for PostgreSQL
- **Containerised** — Dockerfile + `render.yaml` for one-click Render deployment

---

## Local Development

### Prerequisites

- **Python 3.12+** — https://www.python.org/downloads/
- **Docker Desktop** — https://www.docker.com/products/docker-desktop/

### Setup

```powershell
git clone https://github.com/RavalDhruv21/rate-limited-gateway.git
cd rate-limited-gateway
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
```

Open `.env` and set `JWT_SECRET` and `ADMIN_API_KEY` to random strings:
```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

```powershell
docker compose up -d              # start Redis + PostgreSQL
python -m alembic upgrade head    # create tables
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000/docs` to explore the API interactively.

---

## Deployment (Free — Render + Neon + Upstash)

All three services are **free with no credit card required**.

| Service | Role | Free tier |
|---|---|---|
| [Render.com](https://render.com) | App hosting | 750 hrs/mo |
| [Neon](https://neon.tech) | PostgreSQL | 512 MB, forever free |
| [Upstash](https://upstash.com) | Redis | 500K commands/month |

### Step 1 — External services

1. **Neon**: create a project → copy the `DATABASE_URL` (postgres connection string)
2. **Upstash**: create a Redis database → copy the `REDIS_URL`

### Step 2 — Deploy to Render

1. Push this repo to GitHub
2. Render → **New Web Service** → connect your repo
3. Runtime: **Docker** (Render detects the `Dockerfile` automatically)
4. Set environment variables in the Render dashboard:

```
DATABASE_URL   = <from Neon>
REDIS_URL      = <from Upstash>
APP_ENV        = production
JWT_SECRET     = <generate: python -c "import secrets; print(secrets.token_urlsafe(32))">
ADMIN_API_KEY  = <generate same way>
UPSTREAM_BASE_URL = https://httpbin.org
```

5. Click **Deploy** — Render runs `alembic upgrade head` then starts gunicorn automatically.

Your gateway is live at `https://your-service.onrender.com`.

---

## Example Usage

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

# Check upstream health + adaptive rate limiting status
Invoke-RestMethod -Uri http://localhost:8000/admin/upstream-health -Headers $adminHeaders

# Prometheus metrics
Invoke-RestMethod -Uri http://localhost:8000/metrics -Headers $adminHeaders
```

---

## API Reference

### Public

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness probe. Returns `{"status": "ok"}`. |
| GET | `/ready` | Readiness probe. Checks Redis + Postgres. Returns 200 or 503. |
| POST | `/auth/token` | Mint a JWT. Requires `X-Admin-Key` in production. |

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
| GET | `/admin/upstream-health` | Upstream error rate + adaptive factor. |
| GET | `/metrics` | Prometheus metrics (admin key required in production). |

### Response Headers

| Header | Meaning |
|---|---|
| `X-Request-ID` | Unique request ID, propagated to logs. |
| `X-RateLimit-Limit` | Applicable rate limit (may be halved if upstream is degraded). |
| `X-RateLimit-Remaining` | Tokens remaining in bucket. |
| `X-RateLimit-Policy` | IETF standard: `60;w=60` = 60 req per 60s window. |
| `X-RateLimit-Degraded` | Present (`true`) when Redis was unreachable (fail-open). |
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

## Observability

### Prometheus

`GET /metrics` returns Prometheus text format. Connect any Prometheus instance to scrape it.

Key metrics:
- `http_requests_total{status, handler, method}` — request counter
- `http_request_duration_seconds_bucket` — latency histogram (P50/P95/P99)
- `gateway_requests_allowed_total{tier}` — allowed requests per tier
- `gateway_requests_limited_total{tier}` — blocked requests per tier

### Grafana

Import `grafana/dashboard.json` into any Grafana instance (File → Import → Upload JSON).
Select your Prometheus datasource. The dashboard immediately shows:
- Request rate by tier
- Rate-limited % over time
- Upstream error rate
- P50 / P95 / P99 latency

---

## Project Structure

```
rate-limited-gateway/
├── app/
│   ├── main.py                  # FastAPI factory, middleware order, lifespan
│   ├── dependencies.py          # DI providers — rate limiter, log store, Redis
│   ├── core/
│   │   ├── config.py            # Typed settings from .env + production validator
│   │   ├── logging.py           # Structured JSON log formatter
│   │   ├── security.py          # JWT encode/decode
│   │   └── errors.py            # Exception hierarchy + error shape
│   ├── models/
│   │   ├── db.py                # SQLAlchemy ORM tables
│   │   └── schemas.py           # Pydantic API contracts
│   ├── infra/
│   │   ├── database.py          # Async engine + session factory
│   │   ├── circuit_breaker.py   # Async circuit breaker for upstream
│   │   ├── rate_limiter/
│   │   │   ├── base.py          # RateLimiter abstract interface
│   │   │   ├── algorithms.py    # Pure token-bucket math
│   │   │   └── redis_limiter.py # Redis + Lua atomic implementation (fail-open)
│   │   └── log_store/
│   │       ├── base.py               # LogStore abstract interface
│   │       └── postgres_log_store.py # SQLAlchemy + PostgreSQL implementation
│   ├── services/
│   │   ├── rate_limit_service.py     # Limit resolution + health factor
│   │   ├── upstream_health.py        # Sliding-window error rate + adaptive factor
│   │   ├── auth_service.py           # Token issuance
│   │   └── logging_service.py        # Fire-and-forget log writes
│   ├── middleware/
│   │   ├── security.py          # CORS + TrustedHost + security headers
│   │   ├── metrics.py           # Prometheus instrumentation
│   │   ├── auth.py              # JWT validation
│   │   ├── rate_limit.py        # Rate limit enforcement + IETF headers
│   │   ├── logging.py           # Request logging
│   │   └── request_id.py        # Request ID assignment
│   ├── routes/
│   │   ├── health.py            # /health + /ready
│   │   ├── auth.py              # /auth/token
│   │   ├── admin.py             # /admin/* endpoints
│   │   └── proxy.py             # Catch-all proxy with circuit breaker
│   └── utils/
│       └── time.py
├── grafana/
│   └── dashboard.json           # Importable Grafana dashboard
├── migrations/                  # Alembic schema migrations
├── tests/                       # Unit + integration tests
├── scripts/
│   ├── start.sh                 # Production entrypoint (migrations → gunicorn)
│   ├── init_db.py               # CLI: initialize database
│   └── generate_token.py        # CLI: mint a JWT for testing
├── Dockerfile                   # Multi-stage production image
├── docker-compose.yml           # Local Redis + PostgreSQL
├── render.yaml                  # Render.com deployment blueprint
├── .env.example                 # Environment variable template
└── pyproject.toml               # Dependencies + tool config
```

---

## Testing

```powershell
pytest -v
pytest --cov=app --cov-report=term-missing
```

Tests require running Redis and PostgreSQL (`docker compose up -d`).
Each test gets a clean slate: Postgres tables are created/dropped per test,
and the Redis DB is flushed between tests.

Levels covered:
- **Unit** — token bucket math, JWT primitives. Milliseconds per test.
- **Integration** — full HTTP stack via httpx ASGI transport. Verifies middleware ordering, error shapes, and the 429-not-500 regression.

---

## License

MIT — see [LICENSE](LICENSE).
