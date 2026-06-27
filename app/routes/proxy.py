"""
Catch-all proxy route.

For any path not matched by /auth, /admin, /health, /docs, this route
forwards the request to UPSTREAM_BASE_URL and streams the response
back.

By the time we get here:
  - AuthMiddleware has validated the JWT.
  - RateLimitMiddleware has checked quotas.
  - LoggingMiddleware is timing the round-trip.

The proxy strips sensitive headers (Authorization, Host) on the way
out — the upstream shouldn't see the user's bearer token, and Host
must match the upstream's hostname.
"""

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.core.config import settings
from app.core.errors import UpstreamError, UpstreamTimeout

router = APIRouter(tags=["proxy"])

# Headers we never forward.
# - host: must match the upstream's, set automatically by httpx.
# - authorization: our user's JWT, not for the upstream.
# - content-length: httpx recalculates based on what we send.
_STRIP_REQUEST_HEADERS = {"host", "authorization", "content-length"}

# A shared httpx client. In a polished app, we'd create this in main.py's
# lifespan and close it on shutdown so the connection pool is shared
# across requests. We'll wire that up in main.py.
# For now, a module-level singleton (see main.py — we'll attach the
# client to app.state and use it from there).


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def proxy(path: str, request: Request) -> Response:
    """
    Forward the request to UPSTREAM_BASE_URL/{path}.

    Streams the response body back. Adds X-Forwarded-For with the
    client's IP for upstream's awareness.
    """
    # Build the target URL: UPSTREAM_BASE_URL + path + query string.
    upstream = settings.upstream_base_url.rstrip("/") + "/" + path
    if request.url.query:
        upstream = f"{upstream}?{request.url.query}"

    # Filter outgoing headers.
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in _STRIP_REQUEST_HEADERS
    }
    if request.client:
        headers["X-Forwarded-For"] = request.client.host

    # Read body once. For very large uploads this should be streamed,
    # but for typical API payloads reading it upfront is simpler.
    body = await request.body()

    # Use the shared client from app.state (set up in main.py's lifespan).
    client: httpx.AsyncClient = request.app.state.http_client

    try:
        upstream_response = await client.request(
            method=request.method,
            url=upstream,
            headers=headers,
            content=body,
            timeout=30.0,
        )
    except httpx.TimeoutException as exc:
        raise UpstreamTimeout("Upstream backend timed out.") from exc
    except httpx.RequestError as exc:
        raise UpstreamError(f"Upstream backend error: {exc}") from exc

    # Pass response headers through, stripping hop-by-hop headers
    # (these are connection-specific and shouldn't be relayed).
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