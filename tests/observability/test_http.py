import logging

import httpx
import pytest
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from mcp.types import CallToolResult

from travel_agent.observability.http import (
    ExternalHttpRequest,
    capture_external_http_requests,
    configure_safe_http_logging,
    observed_request,
)
from travel_agent.observability.mcp import (
    observed_text_result,
    preserve_observability_metadata,
)


@pytest.mark.asyncio
async def test_observed_request_captures_sanitized_metadata() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == "secret-api-key"
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        with capture_external_http_requests() as observations:
            response = await observed_request(
                client,
                "GET",
                "https://example.com/v1/weather?key=secret-api-key",
                provider="example-weather",
            )

    assert response.status_code == 200
    assert len(observations) == 1
    observation = observations[0]
    assert observation.provider == "example-weather"
    assert observation.method == "GET"
    assert observation.host == "example.com"
    assert observation.path == "/v1/weather"
    assert observation.status_code == 200
    assert observation.succeeded is True
    assert "secret-api-key" not in observation.model_dump_json()


@pytest.mark.asyncio
async def test_observed_request_records_transport_failure() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network unavailable")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        with capture_external_http_requests() as observations:
            with pytest.raises(httpx.ConnectError):
                await observed_request(
                    client,
                    "POST",
                    "https://routing.example.com/route",
                    provider="example-route",
                    max_attempts=1,
                )

    assert len(observations) == 1
    observation = observations[0]
    assert observation.status_code is None
    assert observation.succeeded is False
    assert observation.path == "/route"
    assert observation.attempt == 1


@pytest.mark.asyncio
async def test_observed_request_retries_transport_failure_once() -> None:
    attempts = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("temporary network failure")
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        with capture_external_http_requests() as observations:
            response = await observed_request(
                client,
                "GET",
                "https://example.com/data",
                provider="example",
                retry_backoff_seconds=0,
            )

    assert response.status_code == 200
    assert [item.attempt for item in observations] == [1, 2]
    assert [item.succeeded for item in observations] == [False, True]


@pytest.mark.asyncio
async def test_observed_request_retries_server_error_once() -> None:
    attempts = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        status_code = 503 if attempts == 1 else 200
        return httpx.Response(status_code, json={"attempt": attempts})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        with capture_external_http_requests() as observations:
            response = await observed_request(
                client,
                "GET",
                "https://example.com/data",
                provider="example",
                retry_backoff_seconds=0,
            )

    assert response.status_code == 200
    assert [item.status_code for item in observations] == [503, 200]
    assert [item.attempt for item in observations] == [1, 2]


@pytest.mark.asyncio
async def test_observed_request_retries_rate_limit_once() -> None:
    attempts = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        status_code = 429 if attempts == 1 else 200
        return httpx.Response(status_code, json={"attempt": attempts})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        with capture_external_http_requests() as observations:
            response = await observed_request(
                client,
                "GET",
                "https://example.com/data",
                provider="example",
                retry_backoff_seconds=0,
            )

    assert response.status_code == 200
    assert [item.status_code for item in observations] == [429, 200]


@pytest.mark.asyncio
async def test_observed_request_does_not_retry_regular_client_error() -> None:
    attempts = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, json={"ok": False})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        with capture_external_http_requests() as observations:
            response = await observed_request(
                client,
                "GET",
                "https://example.com/data",
                provider="example",
                retry_backoff_seconds=0,
            )

    assert response.status_code == 400
    assert attempts == 1
    assert len(observations) == 1


@pytest.mark.asyncio
async def test_observed_request_can_redact_sensitive_url_path() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        with capture_external_http_requests() as observations:
            await observed_request(
                client,
                "GET",
                "https://routing.example.com/route/113.9,22.5;114.0,22.6",
                provider="example-route",
                observed_path="/route/{coordinates}",
            )

    assert observations[0].path == "/route/{coordinates}"
    assert "113.9" not in observations[0].model_dump_json()


@pytest.mark.asyncio
async def test_mcp_interceptor_preserves_hidden_observability() -> None:
    request = MCPToolCallRequest(
        name="query_current_weather",
        args={"city": "深圳"},
        server_name="weather",
    )
    observation = ExternalHttpRequest(
        provider="open-meteo-weather",
        method="GET",
        host="api.open-meteo.com",
        path="/v1/forecast",
        status_code=200,
        latency_ms=10,
        succeeded=True,
    )

    async def handler(_request: MCPToolCallRequest) -> CallToolResult:
        return observed_text_result("天气业务结果", [observation])

    result = await preserve_observability_metadata(request, handler)

    assert result.content[0].text == "天气业务结果"
    assert result.structuredContent == {
        "observability": {
            "external_http_request_count": 1,
            "external_http_requests": [observation.model_dump()],
        }
    }


def test_configure_safe_http_logging_suppresses_request_urls() -> None:
    httpx_logger = logging.getLogger("httpx")
    httpcore_logger = logging.getLogger("httpcore")
    previous_httpx_level = httpx_logger.level
    previous_httpcore_level = httpcore_logger.level
    try:
        httpx_logger.setLevel(logging.INFO)
        httpcore_logger.setLevel(logging.DEBUG)

        configure_safe_http_logging()

        assert httpx_logger.level == logging.WARNING
        assert httpcore_logger.level == logging.WARNING
    finally:
        httpx_logger.setLevel(previous_httpx_level)
        httpcore_logger.setLevel(previous_httpcore_level)
