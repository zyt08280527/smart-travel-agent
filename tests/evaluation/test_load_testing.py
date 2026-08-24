import pytest

from travel_agent.evaluation.load_testing import (
    LoadRequestResult,
    nearest_rank_percentile,
    summarize_load_test,
)


def test_nearest_rank_p95() -> None:
    assert nearest_rank_percentile(list(range(1, 21)), 95) == 19


def test_summarizes_latency_and_failures() -> None:
    results = [
        LoadRequestResult(request_id=1, latency_ms=100, succeeded=True, status_code=200),
        LoadRequestResult(request_id=2, latency_ms=300, succeeded=False, status_code=500),
        LoadRequestResult(request_id=3, latency_ms=200, succeeded=True, status_code=200),
    ]

    summary = summarize_load_test(results, concurrency=2, duration_s=1.5)

    assert summary.average_latency_ms == 200
    assert summary.p95_latency_ms == 300
    assert summary.failure_rate == pytest.approx(1 / 3)
    assert summary.throughput_rps == 2
