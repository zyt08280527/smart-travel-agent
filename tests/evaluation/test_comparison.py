from datetime import UTC, datetime

import pytest

from travel_agent.evaluation.comparison import (
    ComparisonThresholds,
    compare_reports,
)
from travel_agent.evaluation.reporting import (
    EvalCaseMeasurement,
    EvalRunReport,
)
from travel_agent.evaluation.scoring import (
    EvalCaseResult,
    EvalMetrics,
)


def _report(
    *,
    tool_accuracy: float = 1,
    parameter_accuracy: float = 1,
    completion_rate: float = 1,
    latency_ms: float = 100,
    tokens: int = 100,
    blocked: int = 0,
    retries: int = 0,
    failed_attempts: int = 0,
    dataset_sha256: str = "a" * 64,
) -> EvalRunReport:
    completed = completion_rate == 1
    result = EvalCaseResult(
        name="comparison_case",
        category="weather",
        tool_selection_correct=tool_accuracy == 1,
        parameter_correct=parameter_accuracy == 1,
        task_completed=completed,
        actual_tool_sequence=["query_current_weather"],
    )
    metrics = EvalMetrics(
        total_cases=1,
        tool_selection_correct=int(tool_accuracy == 1),
        tool_selection_accuracy=tool_accuracy,
        parameter_cases=1,
        parameter_correct=int(parameter_accuracy == 1),
        parameter_accuracy=parameter_accuracy,
        scored_task_cases=1,
        infrastructure_blocked_cases=blocked,
        completed_cases=int(completed),
        task_completion_rate=completion_rate,
    )
    case = EvalCaseMeasurement(
        result=result,
        latency_ms=latency_ms,
        model_calls=1,
        tool_calls=1,
        input_tokens=tokens,
        output_tokens=0,
        total_tokens=tokens,
    )
    now = datetime(2026, 7, 30, tzinfo=UTC)
    return EvalRunReport(
        dataset_schema_version=1,
        dataset_sha256=dataset_sha256,
        model_name="qwen-plus",
        started_at=now,
        finished_at=now,
        metrics=metrics,
        total_latency_ms=latency_ms,
        average_case_latency_ms=latency_ms,
        model_calls=1,
        tool_calls=1,
        input_tokens=tokens,
        output_tokens=0,
        total_tokens=tokens,
        external_http_request_count=1,
        external_http_retry_count=retries,
        external_http_failed_attempt_count=failed_attempts,
        external_http_requests=[],
        cases=[case],
    )


def test_compare_reports_passes_small_cost_changes() -> None:
    comparison = compare_reports(
        _report(latency_ms=100, tokens=100),
        _report(latency_ms=110, tokens=105),
    )

    assert comparison.has_regression is False
    assert comparison.regressions == []
    assert comparison.average_case_latency_ms.relative_delta == pytest.approx(
        0.1
    )


def test_compare_reports_detects_accuracy_and_cost_regressions() -> None:
    comparison = compare_reports(
        _report(),
        _report(
            tool_accuracy=0,
            completion_rate=0,
            latency_ms=130,
            tokens=120,
        ),
    )

    assert comparison.has_regression is True
    assert "工具选择正确率下降" in comparison.regressions
    assert "任务完成率下降" in comparison.regressions
    assert "平均案例耗时增加超过 20%" in comparison.regressions
    assert "总 Token 增加超过 10%" in comparison.regressions


def test_compare_reports_warns_on_external_instability() -> None:
    comparison = compare_reports(
        _report(),
        _report(blocked=1, retries=1, failed_attempts=1),
    )

    assert comparison.has_regression is False
    assert comparison.warnings == [
        "外部服务阻塞案例增加",
        "HTTP 重试次数增加",
        "HTTP 失败尝试次数增加",
    ]


def test_compare_reports_rejects_different_datasets() -> None:
    with pytest.raises(ValueError, match="同一评测数据集"):
        compare_reports(
            _report(dataset_sha256="a" * 64),
            _report(dataset_sha256="b" * 64),
        )


def test_compare_reports_accepts_custom_thresholds() -> None:
    comparison = compare_reports(
        _report(latency_ms=100),
        _report(latency_ms=106),
        ComparisonThresholds(latency_increase_ratio=0.05),
    )

    assert "平均案例耗时增加超过 5%" in comparison.regressions
