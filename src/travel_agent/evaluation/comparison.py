"""Compare two Agent evaluation reports and detect regressions."""

from pathlib import Path

from pydantic import BaseModel, Field

from travel_agent.evaluation.reporting import EvalRunReport


class ComparisonThresholds(BaseModel):
    """Allowed relative cost increases before declaring a regression."""

    latency_increase_ratio: float = Field(default=0.20, ge=0)
    token_increase_ratio: float = Field(default=0.10, ge=0)


class MetricComparison(BaseModel):
    """Baseline, candidate, and delta for one numeric metric."""

    baseline: float
    candidate: float
    absolute_delta: float
    relative_delta: float | None


class EvalReportComparison(BaseModel):
    """Comparable metrics plus deterministic regression diagnostics."""

    tool_selection_accuracy: MetricComparison
    parameter_accuracy: MetricComparison
    task_completion_rate: MetricComparison
    average_case_latency_ms: MetricComparison
    total_tokens: MetricComparison
    infrastructure_blocked_cases: MetricComparison
    external_http_retry_count: MetricComparison
    external_http_failed_attempt_count: MetricComparison
    regressions: list[str]
    warnings: list[str]

    @property
    def has_regression(self) -> bool:
        return bool(self.regressions)


def load_eval_report(path: Path) -> EvalRunReport:
    """Load one UTF-8 evaluation report using the current compatible schema."""
    return EvalRunReport.model_validate_json(path.read_text(encoding="utf-8"))


def compare_reports(
    baseline: EvalRunReport,
    candidate: EvalRunReport,
    thresholds: ComparisonThresholds | None = None,
) -> EvalReportComparison:
    """Compare reports produced from the same versioned evaluation dataset."""
    if baseline.dataset_sha256 != candidate.dataset_sha256:
        raise ValueError("只能比较使用同一评测数据集生成的报告")
    thresholds = thresholds or ComparisonThresholds()

    tool_accuracy = _compare_metric(
        baseline.metrics.tool_selection_accuracy,
        candidate.metrics.tool_selection_accuracy,
    )
    parameter_accuracy = _compare_metric(
        baseline.metrics.parameter_accuracy,
        candidate.metrics.parameter_accuracy,
    )
    completion_rate = _compare_metric(
        baseline.metrics.task_completion_rate,
        candidate.metrics.task_completion_rate,
    )
    latency = _compare_metric(
        baseline.average_case_latency_ms,
        candidate.average_case_latency_ms,
    )
    tokens = _compare_metric(baseline.total_tokens, candidate.total_tokens)
    blocked = _compare_metric(
        baseline.metrics.infrastructure_blocked_cases,
        candidate.metrics.infrastructure_blocked_cases,
    )
    retries = _compare_metric(
        baseline.external_http_retry_count,
        candidate.external_http_retry_count,
    )
    failed_attempts = _compare_metric(
        baseline.external_http_failed_attempt_count,
        candidate.external_http_failed_attempt_count,
    )

    regressions: list[str] = []
    _append_accuracy_regression(
        regressions,
        "工具选择正确率",
        tool_accuracy,
    )
    _append_accuracy_regression(
        regressions,
        "参数正确率",
        parameter_accuracy,
    )
    _append_accuracy_regression(
        regressions,
        "任务完成率",
        completion_rate,
    )
    if (
        latency.relative_delta is not None
        and latency.relative_delta > thresholds.latency_increase_ratio
    ):
        regressions.append(
            "平均案例耗时增加超过 "
            f"{thresholds.latency_increase_ratio:.0%}"
        )
    if (
        tokens.relative_delta is not None
        and tokens.relative_delta > thresholds.token_increase_ratio
    ):
        regressions.append(
            f"总 Token 增加超过 {thresholds.token_increase_ratio:.0%}"
        )

    warnings: list[str] = []
    if blocked.absolute_delta > 0:
        warnings.append("外部服务阻塞案例增加")
    if retries.absolute_delta > 0:
        warnings.append("HTTP 重试次数增加")
    if failed_attempts.absolute_delta > 0:
        warnings.append("HTTP 失败尝试次数增加")

    return EvalReportComparison(
        tool_selection_accuracy=tool_accuracy,
        parameter_accuracy=parameter_accuracy,
        task_completion_rate=completion_rate,
        average_case_latency_ms=latency,
        total_tokens=tokens,
        infrastructure_blocked_cases=blocked,
        external_http_retry_count=retries,
        external_http_failed_attempt_count=failed_attempts,
        regressions=regressions,
        warnings=warnings,
    )


def _compare_metric(baseline: float, candidate: float) -> MetricComparison:
    absolute_delta = candidate - baseline
    relative_delta = absolute_delta / baseline if baseline != 0 else None
    return MetricComparison(
        baseline=baseline,
        candidate=candidate,
        absolute_delta=absolute_delta,
        relative_delta=relative_delta,
    )


def _append_accuracy_regression(
    regressions: list[str],
    label: str,
    metric: MetricComparison,
) -> None:
    if metric.absolute_delta < 0:
        regressions.append(f"{label}下降")
