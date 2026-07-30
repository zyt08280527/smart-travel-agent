"""Compare two Agent behavior evaluation reports."""

import argparse
from pathlib import Path

from travel_agent.evaluation.comparison import (
    EvalReportComparison,
    compare_reports,
    load_eval_report,
)

DEFAULT_REPORT_DIRECTORY = Path("artifacts/evals")


def _latest_two_reports() -> tuple[Path, Path]:
    reports = sorted(DEFAULT_REPORT_DIRECTORY.glob("agent_behavior_*.json"))
    if len(reports) < 2:
        raise ValueError("至少需要两份评测报告才能进行对比")
    return reports[-2], reports[-1]


def _format_delta(value: float, *, percent: bool = False) -> str:
    if percent:
        return f"{value:+.1%}"
    return f"{value:+.2f}"


def _print_comparison(
    baseline_path: Path,
    candidate_path: Path,
    comparison: EvalReportComparison,
) -> None:
    print(f"基线报告: {baseline_path}")
    print(f"候选报告: {candidate_path}")
    print("\n========== 正确率 ==========")
    for label, metric in (
        ("工具选择", comparison.tool_selection_accuracy),
        ("参数", comparison.parameter_accuracy),
        ("任务完成", comparison.task_completion_rate),
    ):
        print(
            f"{label}: {metric.baseline:.1%} -> {metric.candidate:.1%} "
            f"({_format_delta(metric.absolute_delta, percent=True)})"
        )

    print("\n========== 成本与延迟 ==========")
    latency = comparison.average_case_latency_ms
    latency_relative = latency.relative_delta or 0
    print(
        "平均案例耗时: "
        f"{latency.baseline / 1000:.2f}s -> "
        f"{latency.candidate / 1000:.2f}s "
        f"({_format_delta(latency_relative, percent=True)})"
    )
    tokens = comparison.total_tokens
    token_relative = tokens.relative_delta or 0
    print(
        f"总 Token: {tokens.baseline:.0f} -> {tokens.candidate:.0f} "
        f"({_format_delta(token_relative, percent=True)})"
    )

    print("\n========== 稳定性 ==========")
    for label, metric in (
        ("外部服务阻塞", comparison.infrastructure_blocked_cases),
        ("HTTP 重试", comparison.external_http_retry_count),
        ("HTTP 失败尝试", comparison.external_http_failed_attempt_count),
    ):
        print(
            f"{label}: {metric.baseline:.0f} -> {metric.candidate:.0f} "
            f"({_format_delta(metric.absolute_delta)})"
        )

    print("\n========== 对比结论 ==========")
    if comparison.regressions:
        print("REGRESSION")
        for reason in comparison.regressions:
            print(f"- {reason}")
    else:
        print("PASS：未发现超过阈值的回退")
    for warning in comparison.warnings:
        print(f"WARNING：{warning}")


def main() -> None:
    """Parse report paths, compare them, and return a CI-friendly exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path, nargs="?")
    parser.add_argument("candidate", type=Path, nargs="?")
    args = parser.parse_args()
    if (args.baseline is None) != (args.candidate is None):
        parser.error("baseline 和 candidate 必须同时提供")
    baseline_path, candidate_path = (
        (args.baseline, args.candidate)
        if args.baseline is not None and args.candidate is not None
        else _latest_two_reports()
    )
    baseline = load_eval_report(baseline_path)
    candidate = load_eval_report(candidate_path)
    comparison = compare_reports(baseline, candidate)
    _print_comparison(baseline_path, candidate_path, comparison)
    if comparison.has_regression:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
