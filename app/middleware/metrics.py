"""
Prometheus metrics for the gateway.

Uses prometheus-fastapi-instrumentator to auto-instrument all routes
(request count, latency histograms, in-flight requests).

Two custom counters track rate-limiting decisions per tier:
  gateway_requests_allowed_total{tier}
  gateway_requests_limited_total{tier}

These are incremented by rate_limit.py middleware. They're defined here
(imported from here) so there's one canonical registry.

The /metrics endpoint is exposed at startup and protected by the admin
key in production — call setup_metrics(app) inside create_app().
"""

from prometheus_client import Counter
from prometheus_fastapi_instrumentator import Instrumentator

# ─── Custom counters ───────────────────────────────────────

REQUESTS_ALLOWED = Counter(
    "gateway_requests_allowed_total",
    "Requests allowed through the rate limiter",
    ["tier"],
)

REQUESTS_LIMITED = Counter(
    "gateway_requests_limited_total",
    "Requests blocked by the rate limiter",
    ["tier"],
)


# ─── Instrumentation setup ─────────────────────────────────

def setup_metrics(app) -> None:
    """
    Instrument the FastAPI app and expose /metrics.

    Call once inside create_app() after routes are registered.
    The /metrics route is added here; it returns Prometheus text format.
    """
    instrumentator = Instrumentator(
        should_group_status_codes=True,
        excluded_handlers=["/metrics", "/health", "/ready"],
    )
    instrumentator.instrument(app)

    instrumentator.expose(app, endpoint="/metrics")
