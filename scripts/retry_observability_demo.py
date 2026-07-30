"""Demonstrate one transient HTTP failure followed by a successful retry."""

import asyncio
import json

import httpx

from travel_agent.observability.http import (
    capture_external_http_requests,
    observed_request,
)


async def main() -> None:
    """Simulate a timeout on attempt one and success on attempt two."""
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("模拟第一次请求超时", request=request)
        return httpx.Response(200, json={"message": "第二次请求成功"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        with capture_external_http_requests() as observations:
            response = await observed_request(
                client,
                "GET",
                "https://example.com/v1/demo?api_key=secret",
                provider="retry-demo",
                retry_backoff_seconds=0,
            )

    print("\n========== 最终业务响应 ==========")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    print("\n========== HTTP 尝试记录 ==========")
    print(
        json.dumps(
            [item.model_dump(mode="json") for item in observations],
            ensure_ascii=False,
            indent=2,
        )
    )
    print("\n第一次失败被记录，第二次成功后业务继续执行。")


if __name__ == "__main__":
    asyncio.run(main())
