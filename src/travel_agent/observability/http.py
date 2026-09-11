"""Capture external HTTP request metrics without logging secrets."""

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from typing import Any

import httpx
from pydantic import BaseModel, Field


class ExternalHttpRequest(BaseModel):
    """One sanitized external HTTP request observation."""

    provider: str = Field(min_length=1)
    method: str = Field(min_length=1)
    host: str = Field(min_length=1)
    path: str = Field(min_length=1)
    status_code: int | None = None
    latency_ms: float = Field(ge=0)
    succeeded: bool
    attempt: int = Field(default=1, ge=1)


_request_collector: ContextVar[list[ExternalHttpRequest] | None] = ContextVar(
    "external_http_request_collector",
    default=None,
)


def configure_safe_http_logging() -> None:
    """Keep third-party URLs and query credentials out of process logs."""
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


@contextmanager
def capture_external_http_requests() -> Iterator[list[ExternalHttpRequest]]:
    """Collect observations in the current async task and child contexts."""
    observations: list[ExternalHttpRequest] = []
    token = _request_collector.set(observations)
    try:
        yield observations
    finally:
        _request_collector.reset(token)


async def observed_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    provider: str,
    observed_path: str | None = None,
    max_attempts: int = 2,
    retry_backoff_seconds: float = 0.25,
    **kwargs: Any,
) -> httpx.Response:
    """Execute, selectively retry, and record sanitized request attempts."""
    if max_attempts < 1:
        raise ValueError("max_attempts 必须大于等于 1")
    if retry_backoff_seconds < 0:
        raise ValueError("retry_backoff_seconds 不能小于 0")

    parsed_url = httpx.URL(url)
    safe_path = observed_path or parsed_url.path
    for attempt in range(1, max_attempts + 1):
        started = perf_counter()
        response: httpx.Response | None = None
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TransportError:
            _record_attempt(
                provider=provider,
                method=method,
                host=parsed_url.host,
                path=safe_path,
                response=None,
                started=started,
                attempt=attempt,
            )
            if attempt == max_attempts:
                raise
        else:
            _record_attempt(
                provider=provider,
                method=method,
                host=parsed_url.host,
                path=safe_path,
                response=response,
                started=started,
                attempt=attempt,
            )
            if not _is_retryable_status(response.status_code):
                return response
            if attempt == max_attempts:
                return response

        await asyncio.sleep(retry_backoff_seconds * (2 ** (attempt - 1)))

    raise RuntimeError("HTTP 重试循环意外结束")


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _record_attempt(
    *,
    provider: str,
    method: str,
    host: str,
    path: str,
    response: httpx.Response | None,
    started: float,
    attempt: int,
) -> None:
    collector = _request_collector.get()
    if collector is None:
        return
    collector.append(
        ExternalHttpRequest(
            provider=provider,
            method=method.upper(),
            host=host,
            path=path,
            status_code=response.status_code if response is not None else None,
            latency_ms=(perf_counter() - started) * 1000,
            succeeded=response.is_success if response is not None else False,
            attempt=attempt,
        )
    )
