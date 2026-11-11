"""Small in-process sliding-window rate limiter.

For multi-instance deployments replace the store with Redis; the dependency
interface stays the same.
"""
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, Request, status

from app.config import settings


class RateLimiter:
    def __init__(self) -> None:
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window_seconds:
                q.popleft()
            if len(q) >= limit:
                retry = int(window_seconds - (now - q[0])) + 1
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many requests. Please wait a moment and try again.",
                    headers={"Retry-After": str(retry)},
                )
            q.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def rate_limit(group: str, limit: int, window_seconds: int = 60):
    def dependency(request: Request) -> None:
        if not settings.rate_limit_enabled:
            return
        client = request.client.host if request.client else "unknown"
        limiter.check(f"{group}:{client}", limit, window_seconds)

    return dependency
