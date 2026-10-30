"""Request logging: one line per interaction, whether it answered or raised.

The line is the facade's whole access log - method, path, status, duration - because a FHIR
endpoint's useful signal is which resource a client asked for and whether it got it. A request
that ends in an unhandled exception is logged at the same shape before the exception continues
to the error handler, so a 500 is never a silent gap in the log.
"""

from __future__ import annotations

import time

from dhis2w_fhir_engine.facade.log import logger
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class RequestLogMiddleware(BaseHTTPMiddleware):
    """Write one log line per request, whether it answered or raised."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Time the request, log its outcome, and let the response or the exception through."""
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            _log_request(request, status_code=500, started=started)
            raise
        _log_request(request, status_code=response.status_code, started=started)
        return response


def _log_request(request: Request, *, status_code: int, started: float) -> None:
    """Log one interaction as `METHOD path?query -> status durationms`."""
    duration_ms = (time.perf_counter() - started) * 1000
    query = request.url.query
    target = f"{request.url.path}?{query}" if query else request.url.path
    logger.info("%s %s -> %d %.1fms", request.method, target, status_code, duration_ms)
