"""Deterministic scoring for Agent behavior evaluation messages."""

import json
from typing import Any

from langchain.messages import AIMessage, ToolMessage
from pydantic import BaseModel, Field

from travel_agent.evaluation.dataset import EvalCase
from travel_agent.presentation import render_user_response


class EvalCaseResult(BaseModel):
    """Component scores and diagnostics for one evaluation case."""

    name: str
    category: str
    tool_selection_correct: bool
    parameter_correct: bool | None
    task_completed: bool | None
    actual_tool_sequence: list[str]
    failures: list[str] = Field(default_factory=list)
    infrastructure_blocked: bool = False
    infrastructure_errors: list[str] = Field(default_factory=list)


class EvalMetrics(BaseModel):
    """Aggregate case-level metrics for one dataset run."""

    total_cases: int
    tool_selection_correct: int
    tool_selection_accuracy: float
    parameter_cases: int
    parameter_correct: int
    parameter_accuracy: float
    scored_task_cases: int
    infrastructure_blocked_cases: int
    completed_cases: int
    task_completion_rate: float


def _tool_json_payloads(message: ToolMessage) -> list[dict[str, Any]]:
    """Parse JSON objects from one tool result message."""
    content_blocks = (
        message.content
        if isinstance(message.content, list)
        else [message.content]
    )
    payloads: list[dict[str, Any]] = []
    for block in content_blocks:
        text = block.get("text") if isinstance(block, dict) else block
        if not isinstance(text, str):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _expected_endpoint_coordinates(
    endpoint_payload: dict[str, Any] | None,
) -> dict[str, object] | None:
    """Extract the first resolved origin and destination coordinates."""
    try:
        origin = endpoint_payload["origin"]["places"][0]
        destination = endpoint_payload["destination"]["places"][0]
        return {
            "origin_latitude": origin["latitude"],
            "origin_longitude": origin["longitude"],
            "destination_latitude": destination["latitude"],
            "destination_longitude": destination["longitude"],
        }
    except (KeyError, IndexError, TypeError):
        return None


def score_case(case: EvalCase, messages: list[object]) -> EvalCaseResult:
    """Score tool selection, parameters, and complete task behavior."""
    failures: list[str] = []
    tool_calls = [
        tool_call
        for message in messages
        if isinstance(message, AIMessage)
        for tool_call in message.tool_calls
    ]
    tool_messages = [
        message for message in messages if isinstance(message, ToolMessage)
    ]
    result_payloads = [
        payload
        for message in tool_messages
        for payload in _tool_json_payloads(message)
    ]
    error_payloads = [
        payload for payload in result_payloads if payload.get("ok") is False
    ]
    infrastructure_errors = (
        []
        if case.expect_tool_error
        else [
            str(payload.get("error") or "外部工具返回未知错误")
            for payload in error_payloads
        ]
    )
    actual_tool_sequence = tuple(call["name"] for call in tool_calls)
    expected_sequence = case.expected_tool_sequence
    if not expected_sequence and case.expected_tool is not None:
        expected_sequence = (case.expected_tool,)
    tool_selection_correct = actual_tool_sequence == expected_sequence

    parameter_correct: bool | None = None
    matching_call: dict[str, Any] | None = None
    if case.expected_tool is None:
        if tool_calls:
            failures.append("该案例不应调用工具")
        if tool_messages:
            failures.append("该案例不应出现工具结果")
    else:
        parameter_correct = False
        matching_call = next(
            (
                call
                for call in tool_calls
                if call["name"] == case.expected_tool
                and all(
                    call["args"].get(key) == value
                    for key, value in case.expected_args.items()
                )
            ),
            None,
        )
        if matching_call is None:
            failures.append(
                f"应使用参数 {case.expected_args!r} 调用 {case.expected_tool}"
            )
        else:
            parameter_correct = True
        if not tool_messages:
            failures.append("调用工具后应出现 ToolMessage")

    if not tool_selection_correct:
        failures.append(
            f"工具调用顺序应为 {expected_sequence!r}，"
            f"实际为 {actual_tool_sequence!r}"
        )

    endpoint_dependent_tool = (
        case.route_tool_from_endpoints or case.planning_tool_from_endpoints
    )
    if endpoint_dependent_tool is not None:
        endpoint_payload = next(
            (
                payload
                for message in tool_messages
                if message.name == "resolve_route_endpoints"
                for payload in _tool_json_payloads(message)
            ),
            None,
        )
        downstream_call = next(
            (
                call
                for call in tool_calls
                if call["name"] == endpoint_dependent_tool
            ),
            None,
        )
        expected_coordinates = _expected_endpoint_coordinates(endpoint_payload)
        if expected_coordinates is None:
            parameter_correct = False
            failures.append("起终点工具结果应包含可用于路线规划的候选坐标")
        else:
            if downstream_call is None:
                parameter_correct = False
                failures.append(
                    "获得起终点坐标后应调用 "
                    f"{endpoint_dependent_tool}"
                )
            elif case.route_tool_from_endpoints is not None and (
                downstream_call["args"] != expected_coordinates
            ):
                parameter_correct = False
                failures.append(
                    "路线工具参数必须与起终点工具返回的第一组候选坐标一致"
                )
            elif case.planning_tool_from_endpoints is not None and not all(
                downstream_call["args"].get(key) == value
                for key, value in expected_coordinates.items()
            ):
                parameter_correct = False
                failures.append(
                    "综合规划工具的坐标参数必须与起终点工具返回的"
                    "第一组候选坐标一致"
                )
            elif case.planning_tool_from_endpoints is not None and not all(
                downstream_call["args"].get(key) == value
                for key, value in case.expected_planning_args.items()
            ):
                parameter_correct = False
                failures.append(
                    "综合规划工具参数必须包含预期业务参数 "
                    f"{case.expected_planning_args!r}"
                )

    if case.expect_tool_error:
        if not error_payloads:
            failures.append("工具结果应包含 ok=false")
        for expected_text in case.expected_tool_error_substrings:
            if not any(
                expected_text in str(payload.get("error", ""))
                for payload in error_payloads
            ):
                failures.append(f"工具错误应包含 {expected_text!r}")

    final_answers = [
        message.content
        for message in messages
        if isinstance(message, AIMessage) and not message.tool_calls
    ]
    final_answer = render_user_response(messages) if final_answers else ""
    if not final_answer:
        failures.append("应生成非空的最终回答")
    if not infrastructure_errors:
        for required_text in case.required_final_substrings:
            if required_text not in final_answer:
                failures.append(f"最终回答应包含 {required_text!r}")
        if case.required_final_any_of and not any(
            text in final_answer for text in case.required_final_any_of
        ):
            failures.append(
                "最终回答至少应包含一个澄清提示词："
                f"{case.required_final_any_of!r}"
            )
    for forbidden_text in case.forbidden_final_substrings:
        if forbidden_text in final_answer:
            failures.append(f"最终回答不应包含 {forbidden_text!r}")

    infrastructure_blocked = bool(infrastructure_errors)
    task_completed: bool | None = not failures
    if infrastructure_blocked and not failures:
        task_completed = None

    return EvalCaseResult(
        name=case.name,
        category=case.category,
        tool_selection_correct=tool_selection_correct,
        parameter_correct=parameter_correct,
        task_completed=task_completed,
        actual_tool_sequence=list(actual_tool_sequence),
        failures=failures,
        infrastructure_blocked=infrastructure_blocked,
        infrastructure_errors=infrastructure_errors,
    )


def summarize_results(results: list[EvalCaseResult]) -> EvalMetrics:
    """Calculate strict case-level aggregate evaluation metrics."""
    if not results:
        raise ValueError("评测结果不能为空")
    parameter_results = [
        result for result in results if result.parameter_correct is not None
    ]
    tool_correct = sum(result.tool_selection_correct for result in results)
    parameter_correct = sum(
        result.parameter_correct is True for result in parameter_results
    )
    scored_task_results = [
        result for result in results if result.task_completed is not None
    ]
    completed = sum(
        result.task_completed is True for result in scored_task_results
    )
    return EvalMetrics(
        total_cases=len(results),
        tool_selection_correct=tool_correct,
        tool_selection_accuracy=tool_correct / len(results),
        parameter_cases=len(parameter_results),
        parameter_correct=parameter_correct,
        parameter_accuracy=(
            parameter_correct / len(parameter_results)
            if parameter_results
            else 0
        ),
        scored_task_cases=len(scored_task_results),
        infrastructure_blocked_cases=sum(
            result.infrastructure_blocked for result in results
        ),
        completed_cases=completed,
        task_completion_rate=(
            completed / len(scored_task_results) if scored_task_results else 0
        ),
    )
