"""逐步展示地点搜索词如何变成 PlaceSearchResult。"""

import argparse
import asyncio
import json

import httpx

from travel_agent.config import get_settings
from travel_agent.services.amap_coordinate import AMAP_USER_AGENT
from travel_agent.services.amap_place import (
    AMAP_PLACE_SEARCH_URL,
    AmapPlaceService,
)


def print_step(number: int, title: str, value: object) -> None:
    """以容易阅读的格式打印一个追踪步骤。"""
    print(f"\n{'=' * 18} 步骤 {number}：{title} {'=' * 18}")
    if isinstance(value, (dict, list)):
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(value)


async def main(raw_query: str) -> None:
    """请求一次真实接口并展示 Service 转换前后的数据。"""
    print_step(1, "用户原始输入", repr(raw_query))

    normalized_query = raw_query.strip()
    print_step(2, "strip() 清理后的搜索词", repr(normalized_query))

    settings = get_settings()
    api_key = settings.amap_api_key.get_secret_value()
    proxy_url = settings.route_proxy_url
    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(
        timeout=timeout,
        proxy=str(proxy_url) if proxy_url is not None else None,
        trust_env=False,
    ) as client:
        response = await client.get(
            AMAP_PLACE_SEARCH_URL,
            params={
                "key": api_key,
                "keywords": normalized_query,
                "page_size": 3,
                "page_num": 1,
            },
            headers={"User-Agent": AMAP_USER_AGENT},
        )
        safe_url = str(response.request.url).replace(api_key, "<REDACTED>")
        print_step(3, "httpx 组装后的请求 URL（Key 已隐藏）", safe_url)
        print_step(4, "地点搜索 HTTP 状态码", response.status_code)
        response.raise_for_status()

        payload = response.json()
        print_step(5, "高德返回的原始 JSON", payload)

        result = AmapPlaceService._parse_results(payload, normalized_query)
        print_step(6, "转换并校验后的 PlaceSearchResult", result.model_dump())
        print_step(7, "可传输的最终 JSON 字符串", result.model_dump_json(indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="  深圳大学  ", help="要追踪的地点搜索词")
    args = parser.parse_args()
    asyncio.run(main(args.query))
