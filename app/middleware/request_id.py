"""
Request ID middleware.

Generates a UUID per request, attaches it to request.state for use
downstream (auth, rate limiter, logging), and adds it to the response
headers so clients can correlate failures.

Lives first in the middleware chain — every other middleware reads
it, so it must be set before they run.
"""

import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a UUID request ID to every request."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        # Honor an incoming X-Request-ID if the client sent one (useful
        # for distributed tracing). Otherwise generate fresh.
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming or f"req_{uuid.uuid4().hex[:16]}"

        # request.state is a free-form namespace per request — attach
        # anything here and downstream code can read it.
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response