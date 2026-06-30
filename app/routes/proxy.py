"""
Catch-all proxy route.

For any path not matched by /auth, /admin, /health, /docs, this route
forwards the request to UPSTREAM_BASE_URL and streams the response back.

By the time we get here:
  - AuthMiddleware has validated the JWT.
  - RateLimitMiddleware has checked quotas.
  - LoggingMiddleware is timing the round-trip.

The proxy strips sensitive headers (Authorization, Host) on the way out.
Each upstream call is wrapped in the circuit breaker: after 5 consecutive
failures the breaker opens and returns 503 immediately for 60s, protecting
both the client (fast fail) and the upstream (no flood during outage).

Upstream results are also recorded for adaptive rate limiting — when error
rate exceeds 20% the gateway automatically halves rate limits to reduce
traffic on a struggling upstream.
"""

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.core.config import settings
from app.core.errors import UpstreamError, UpstreamTimeout
from app.infra.circuit_breaker import CircuitOpenError, upstream_breaker

router = APIRouter(tags=["proxy"])

_STRIP_REQUEST_HEADERS = {"host", "authorization", "content-length"}


async def _do_upstream_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict,
    content: bytes,
) -> httpx.Response:
    """The actual HTTP call — wrapped by the circuit breaker."""
    return await client.request(
        method=method,
        url=url,
        headers=headers,
        content=content,
        timeout=30.0,
    )


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def proxy(path: str, request: Request) -> Response:
    """
    Forward the request to UPSTREAM_BASE_URL/{path}.

    Returns 503 immediately if the circuit breaker is open.
    Records whether the upstream responded successfully for adaptive
    rate limiting.
    """
    upstream = settings.upstream_base_url.rstrip("/") + "/" + path
    if request.url.query:
        upstream = f"{upstream}?{request.url.query}"

    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in _STRIP_REQUEST_HEADERS
    }
    if request.client:
        headers["X-Forwarded-For"] = request.client.host

    body = await request.body()
    client: httpx.AsyncClient = request.app.state.http_client

    # ── Import health recorder (local to avoid circular) ──
    from app.dependencies import get_redis_client
    from app.services.upstream_health import record_upstream_result
    redis = get_redis_client()

    # ── Circuit breaker wraps the upstream call ────────────
    try:
        upstream_response: httpx.Response = await upstream_breaker.call(
            _do_upstream_request, client, request.method, upstream, headers, body
        )
    except CircuitOpenError as exc:
        await record_upstream_result(redis, success=False)
        raise UpstreamError("Upstream circuit open — service temporarily unavailable.") from exc
    except httpx.TimeoutException as exc:
        await record_upstream_result(redis, success=False)
        raise UpstreamTimeout("Upstream backend timed out.") from exc
    except httpx.RequestError as exc:
        await record_upstream_result(redis, success=False)
        raise UpstreamError(f"Upstream backend error: {exc}") from exc

    # Record success/failure for adaptive rate limiting.
    is_server_error = upstream_response.status_code >= 500
    await record_upstream_result(redis, success=not is_server_error)

    hop_by_hop = {
        "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "te", "trailers", "transfer-encoding", "upgrade",
    }
    response_headers = {
        k: v
        for k, v in upstream_response.headers.items()
        if k.lower() not in hop_by_hop
    }

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )
