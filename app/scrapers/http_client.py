from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx
from loguru import logger
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings


class HostThrottle:
    """Simple per-host throttle: enforce a minimum delay between requests."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self._last: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def wait(self, host: str) -> None:
        async with self._locks[host]:
            now = time.monotonic()
            wait_for = self._last[host] + self.delay - now
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._last[host] = time.monotonic()


class HttpClient:
    """Shared async HTTP client with retry, throttle and clear UA.

    Usage:
        async with HttpClient() as c:
            r = await c.get(url)
    """

    def __init__(
        self,
        base_url: str = "",
        extra_headers: Optional[dict[str, str]] = None,
        per_host_delay: Optional[float] = None,
        timeout: Optional[int] = None,
        concurrency: Optional[int] = None,
        max_retries: Optional[int] = None,
    ) -> None:
        self.base_url = base_url
        self.headers = {
            "User-Agent": settings.http_user_agent,
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "en;q=0.9,fr;q=0.8",
        }
        if extra_headers:
            self.headers.update(extra_headers)
        self._client: Optional[httpx.AsyncClient] = None
        self._throttle = HostThrottle(per_host_delay or settings.scrape_per_host_delay)
        self._sem = asyncio.Semaphore(concurrency or settings.scrape_concurrency)
        self._timeout = timeout or settings.scrape_timeout
        self._max_retries = (
            max_retries if max_retries is not None else settings.scrape_max_retries
        )

    async def __aenter__(self) -> "HttpClient":
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=self._timeout,
            follow_redirects=True,
            http2=True,
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def request(
        self, method: str, url: str, **kw: Any
    ) -> httpx.Response:
        assert self._client is not None, "HttpClient must be used as async context"
        host = urlsplit(url if "://" in url else self.base_url + url).netloc

        async def _do() -> httpx.Response:
            await self._throttle.wait(host)
            async with self._sem:
                return await self._client.request(method, url, **kw)

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._max_retries),
                wait=wait_exponential(min=1, max=10),
                retry=retry_if_exception_type(
                    (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
                ),
                reraise=True,
            ):
                with attempt:
                    r = await _do()
                    if r.status_code in (429, 502, 503, 504):
                        logger.warning(
                            "Transient {} on {} {} — retrying", r.status_code, method, url
                        )
                        raise httpx.TimeoutException("transient", request=r.request)
                    return r
        except RetryError as e:  # pragma: no cover
            raise e
        raise RuntimeError("unreachable")

    async def get(self, url: str, **kw: Any) -> httpx.Response:
        return await self.request("GET", url, **kw)

    async def post(self, url: str, **kw: Any) -> httpx.Response:
        return await self.request("POST", url, **kw)
