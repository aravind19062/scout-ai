"""In-memory sliding-window rate limiter for mutating API requests (per client IP).

Single-process scope by design (see docs/SECURITY.md); swap for a Redis-backed limiter when
running multiple API replicas.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, per_minute: int):
        super().__init__(app)
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api"):
            ip = request.client.host if request.client else "unknown"
            now = time.monotonic()
            q = self.hits[ip]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                retry = int(60 - (now - q[0])) + 1
                return JSONResponse(
                    {"error": {"code": "rate_limited", "message": "Too many requests, slow down."}},
                    status_code=429, headers={"Retry-After": str(retry)},
                )
            q.append(now)
        return await call_next(request)
