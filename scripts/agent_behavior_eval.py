"""Evaluate important Agent behaviors with the real model and MCP tool."""

import argparse
import asyncio
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from travel_agent.agent import travel_agent_session
from travel_agent.config import get_settings
from travel_agent.evaluation.dataset import (
    DEFAULT_DATASET_PATH,
    load_eval_dataset,
)
from travel_agent.evaluation.reporting import build_run_report, measure_case
from travel_agent.evaluation.scoring import score_case, summarize_results
from travel_agent.presentation import render_user_response

REPORT_DIRECTORY = Path("artifacts/evals")


def console_safe(value: object) -> str:
    """Make diagnostic text printable on Windows legacy code pages."""
    encoding = sys.stdout.encoding or "utf-8"
    return str(value).encode(encoding, errors="backslashreplace").decode(encoding)


def parse_args() -> argparse.Namespace:
    """Parse optional case filters for fast targeted regression runs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        dest="case_names",
        help="只运行指定案例；可重复传入",
    )
    return parser.parse_args()


async def main(case_names: tuple[str, ...] = ()) -> None:
    """Run every case in one MCP session and print a compact report."""
    dataset = load_eval_dataset()
    cases = dataset.cases
    if case_names:
        requested = set(case_names)
        known = {case.name for case in cases}
        unknown = sorted(requested - known)
        if unknown:
            raise SystemExit(f"未知评测案例: {', '.join(unknown)}")
        cases = [case for case in cases if case.name in requested]
    case_results = []
    case_measurements = []
    started_at = datetime.now(UTC)

    async with travel_agent_session() as (agent, tools):
        print(f"已加载 MCP 工具: {[tool.name for tool in tools]}")

        for case in cases:
            case_started = perf_counter()
            config = {"configurable": {"thread_id": str(uuid4())}}
            result = None
            for message in (case.message, *case.follow_up_messages):
                result = await agent.ainvoke(
                    {"messages": [{"role": "user", "content": message}]},
                    config=config,
                )
            if result is None:
                raise RuntimeError("评测案例没有可执行消息")
            case_result = score_case(case, result["messages"])
            case_results.append(case_result)
            case_measurements.append(
                measure_case(
                    case_result,
                    result["messages"],
                    latency_ms=(perf_counter() - case_started) * 1000,
                )
            )

            if case_result.failures:
                print(f"[FAIL] {case.name}")
                for failure in case_result.failures:
                    print(f"       - {failure}")
                print("\n实际最终回答：")
                print(console_safe(render_user_response(result["messages"])))
                print("\n实际工具调用：")
                for message in result["messages"]:
                    for tool_call in getattr(message, "tool_calls", []):
                        diagnostic = (
                            f"- {tool_call.get('name')}: "
                            f"{tool_call.get('args')}"
                        )
                        print(console_safe(diagnostic))
            elif case_result.infrastructure_blocked:
                print(f"[BLOCKED] {case.name}")
                for error in case_result.infrastructure_errors:
                    print(f"          - 外部工具错误：{error}")
            else:
                print(f"[PASS] {case.name}")

    metrics = summarize_results(case_results)
    print("\n========== 评测指标 ==========")
    print(
        "工具选择正确率: "
        f"{metrics.tool_selection_correct}/{metrics.total_cases} "
        f"({metrics.tool_selection_accuracy:.1%})"
    )
    print(
        "参数正确率: "
        f"{metrics.parameter_correct}/{metrics.parameter_cases} "
        f"({metrics.parameter_accuracy:.1%})"
    )
    print(
        "任务完成率: "
        f"{metrics.completed_cases}/{metrics.scored_task_cases} "
        f"({metrics.task_completion_rate:.1%})"
    )
    print(
        "外部服务阻塞案例: "
        f"{metrics.infrastructure_blocked_cases}/{metrics.total_cases}"
    )
    report = build_run_report(
        dataset_schema_version=dataset.schema_version,
        dataset_sha256=hashlib.sha256(
            DEFAULT_DATASET_PATH.read_bytes()
        ).hexdigest(),
        model_name=get_settings().model_name,
        started_at=started_at,
        finished_at=datetime.now(UTC),
        metrics=metrics,
        cases=case_measurements,
    )
    print("\n========== 运行观测 ==========")
    print(f"总耗时: {report.total_latency_ms / 1000:.2f} 秒")
    print(f"平均案例耗时: {report.average_case_latency_ms / 1000:.2f} 秒")
    print(f"模型调用次数: {report.model_calls}")
    print(f"MCP 工具调用次数: {report.tool_calls}")
    print(
        "Token: "
        f"输入 {report.input_tokens} / 输出 {report.output_tokens} / "
        f"总计 {report.total_tokens}"
    )
    print(
        "外部 HTTP 尝试次数（含重试）: "
        f"{report.external_http_request_count}"
    )
    print(f"HTTP 重试次数: {report.external_http_retry_count}")
    print(
        "HTTP 失败尝试次数: "
        f"{report.external_http_failed_attempt_count}"
    )

    await asyncio.to_thread(
        REPORT_DIRECTORY.mkdir,
        parents=True,
        exist_ok=True,
    )
    timestamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    report_path = REPORT_DIRECTORY / f"agent_behavior_{timestamp}.json"
    await asyncio.to_thread(
        report_path.write_text,
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )
    print(f"评测报告: {report_path}")
    if any(result.task_completed is False for result in case_results):
        raise SystemExit(1)


if __name__ == "__main__":
    arguments = parse_args()
    asyncio.run(main(tuple(arguments.case_names)))
