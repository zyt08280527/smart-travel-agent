"""Run a bounded concurrent load test against the running Agent API."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import httpx

from travel_agent.evaluation.load_testing import (
    LoadRequestResult,
    summarize_load_test,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MESSAGE = "请只回复“并发测试成功”，不要调用工具。"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="对Agent HTTP接口执行简单并发压测")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    args = parser.parse_args()
    if args.requests < 1:
        parser.error("--requests 必须至少为1")
    if args.concurrency < 1:
        parser.error("--concurrency 必须至少为1")
    if args.concurrency > args.requests:
        parser.error("--concurrency 不能大于 --requests")
    return args


async def run_one(
    request_id: int,
    *,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    message: str,
) -> LoadRequestResult:
    async with semaphore:
        started_at = perf_counter()
        try:
            response = await client.post("/api/chat", json={"message": message})
            latency_ms = (perf_counter() - started_at) * 1000
            response.raise_for_status()
            payload = response.json()
            succeeded = payload.get("status") == "completed" and bool(payload.get("answer"))
            return LoadRequestResult(
                request_id=request_id,
                latency_ms=latency_ms,
                succeeded=succeeded,
                status_code=response.status_code,
                error=None if succeeded else "响应缺少completed状态或answer",
            )
        except Exception as exc:
            return LoadRequestResult(
                request_id=request_id,
                latency_ms=(perf_counter() - started_at) * 1000,
                succeeded=False,
                status_code=getattr(getattr(exc, "response", None), "status_code", None),
                error=f"{type(exc).__name__}: {exc}",
            )


async def main() -> None:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    limits = httpx.Limits(
        max_connections=args.concurrency,
        max_keepalive_connections=args.concurrency,
    )
    timeout = httpx.Timeout(args.timeout)
    async with httpx.AsyncClient(base_url=base_url, limits=limits, timeout=timeout) as client:
        health = await client.get("/health")
        health.raise_for_status()
        print(f"Agent API: {base_url}")
        print(f"并发数: {args.concurrency}，总请求数: {args.requests}")

        semaphore = asyncio.Semaphore(args.concurrency)
        started_at = perf_counter()
        results = await asyncio.gather(
            *(
                run_one(
                    request_id,
                    client=client,
                    semaphore=semaphore,
                    message=args.message,
                )
                for request_id in range(1, args.requests + 1)
            )
        )
        duration_s = perf_counter() - started_at

    summary = summarize_load_test(
        results,
        concurrency=args.concurrency,
        duration_s=duration_s,
    )
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "message": args.message,
        "summary": summary.model_dump(),
        "requests": [result.model_dump() for result in results],
    }
    artifacts_dir = PROJECT_ROOT / "artifacts" / "load-tests"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report_path = artifacts_dir / f"agent_load_{timestamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n========== Agent 并发压测 ==========")
    print(f"成功请求: {summary.succeeded_count}/{summary.request_count}")
    print(f"失败率: {summary.failure_rate:.1%}")
    print(f"平均延迟: {summary.average_latency_ms:.2f} ms")
    print(f"P95延迟: {summary.p95_latency_ms:.2f} ms")
    print(f"最小/最大延迟: {summary.min_latency_ms:.2f}/{summary.max_latency_ms:.2f} ms")
    print(f"吞吐量: {summary.throughput_rps:.2f} requests/s")
    print(f"评测报告: {report_path.relative_to(PROJECT_ROOT)}")

    for result in results:
        if not result.succeeded:
            print(f"[FAIL] request={result.request_id}: {result.error}")
    if summary.failed_count:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
