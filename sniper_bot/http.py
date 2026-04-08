from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import aiohttp

from .config import settings
from .rate_limit import AsyncRateLimiter


class HttpClient:
    def __init__(self) -> None:
        self.log = logging.getLogger(self.__class__.__name__)
        self.session: aiohttp.ClientSession | None = None
        self._default_limiter = AsyncRateLimiter(max_calls=6, period_seconds=1.0)

    async def __aenter__(self) -> "HttpClient":
        timeout = aiohttp.ClientTimeout(total=settings.http_timeout)
        self.session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self.session:
            await self.session.close()

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        limiter: AsyncRateLimiter | None = None,
    ) -> Any:
        assert self.session is not None
        limiter = limiter or self._default_limiter
        for attempt in range(settings.http_max_retries + 1):
            try:
                await limiter.acquire()
                async with self.session.get(url, params=params, headers=headers) as resp:
                    if resp.status in {429, 500, 502, 503, 504}:
                        text = await resp.text()
                        await self._sleep_before_retry(resp, attempt, "GET", url, text)
                        continue
                    resp.raise_for_status()
                    return await resp.json(content_type=None)
            except aiohttp.ClientResponseError:
                raise
            except Exception as exc:
                if attempt >= settings.http_max_retries:
                    raise
                sleep_for = settings.http_backoff_base * (2**attempt) + random.uniform(0, 0.35)
                self.log.warning("GET retry %s %s failed: %s", attempt + 1, url, exc)
                await asyncio.sleep(sleep_for)
        raise RuntimeError(f"GET failed after retries: {url}")

    async def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
        limiter: AsyncRateLimiter | None = None,
    ) -> Any:
        assert self.session is not None
        limiter = limiter or self._default_limiter
        for attempt in range(settings.http_max_retries + 1):
            try:
                await limiter.acquire()
                async with self.session.post(url, json=payload, headers=headers) as resp:
                    if resp.status in {429, 500, 502, 503, 504}:
                        text = await resp.text()
                        await self._sleep_before_retry(resp, attempt, "POST", url, text)
                        continue
                    resp.raise_for_status()
                    return await resp.json(content_type=None)
            except aiohttp.ClientResponseError:
                raise
            except Exception as exc:
                if attempt >= settings.http_max_retries:
                    raise
                sleep_for = settings.http_backoff_base * (2**attempt) + random.uniform(0, 0.35)
                self.log.warning("POST retry %s %s failed: %s", attempt + 1, url, exc)
                await asyncio.sleep(sleep_for)
        raise RuntimeError(f"POST failed after retries: {url}")

    async def _sleep_before_retry(
        self,
        resp: aiohttp.ClientResponse,
        attempt: int,
        method: str,
        url: str,
        body: str,
    ) -> None:
        if attempt >= settings.http_max_retries:
            raise aiohttp.ClientResponseError(
                request_info=resp.request_info,
                history=resp.history,
                status=resp.status,
                message=body,
                headers=resp.headers,
            )
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                sleep_for = max(float(retry_after), 0.25)
            except ValueError:
                sleep_for = settings.http_backoff_base * (2**attempt)
        else:
            sleep_for = settings.http_backoff_base * (2**attempt)
        sleep_for += random.uniform(0, 0.35)
        self.log.warning(
            "%s retry %s %s failed: %s, sleeping %.2fs",
            method,
            attempt + 1,
            url,
            body[:300],
            sleep_for,
        )
        await asyncio.sleep(sleep_for)
