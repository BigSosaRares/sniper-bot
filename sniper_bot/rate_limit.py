from __future__ import annotations

import asyncio
import time
from collections import deque


class AsyncRateLimiter:
    def __init__(self, max_calls: int, period_seconds: float) -> None:
        self.max_calls = max(1, max_calls)
        self.period_seconds = max(0.01, period_seconds)
        self._events: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._events and now - self._events[0] >= self.period_seconds:
                    self._events.popleft()
                if len(self._events) < self.max_calls:
                    self._events.append(now)
                    return
                sleep_for = self.period_seconds - (now - self._events[0]) + 0.01
                await asyncio.sleep(max(0.01, sleep_for))
