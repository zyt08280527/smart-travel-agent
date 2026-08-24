"""Small, reproducible load-test statistics for the Agent HTTP API."""

import math
from collections.abc import Sequence

from pydantic import BaseModel, Field


class LoadRequestResult(BaseModel):
    request_id: int = Field(ge=1)
    latency_ms: float = Field(ge=0)
    succeeded: bool
    status_code: int | None = None
    error: str | None = None


class LoadTestSummary(BaseModel):
    request_count: int = Field(ge=1)
    concurrency: int = Field(ge=1)
    succeeded_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    failure_rate: float = Field(ge=0, le=1)
    average_latency_ms: float = Field(ge=0)
    p95_latency_ms: float = Field(ge=0)
    min_latency_ms: float = Field(ge=0)
    max_latency_ms: float = Field(ge=0)
    duration_s: float = Field(ge=0)
    throughput_rps: float = Field(ge=0)


def nearest_rank_percentile(values: Sequence[float], percentile: float) -> float:
    """Return a nearest-rank percentile, suitable for a compact load report."""
    if not values:
        raise ValueError("values must not be empty")
    if not 0 < percentile <= 100:
        raise ValueError("percentile must be in (0, 100]")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize_load_test(
    results: Sequence[LoadRequestResult],
    *,
    concurrency: int,
    duration_s: float,
) -> LoadTestSummary:
    if not results:
        raise ValueError("results must not be empty")
    latencies = [result.latency_ms for result in results]
    succeeded_count = sum(result.succeeded for result in results)
    failed_count = len(results) - succeeded_count
    return LoadTestSummary(
        request_count=len(results),
        concurrency=concurrency,
        succeeded_count=succeeded_count,
        failed_count=failed_count,
        failure_rate=failed_count / len(results),
        average_latency_ms=sum(latencies) / len(latencies),
        p95_latency_ms=nearest_rank_percentile(latencies, 95),
        min_latency_ms=min(latencies),
        max_latency_ms=max(latencies),
        duration_s=duration_s,
        throughput_rps=len(results) / duration_s if duration_s else 0.0,
    )
