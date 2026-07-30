import json

from langchain.messages import AIMessage, ToolMessage

from travel_agent.evaluation.dataset import EvalCase
from travel_agent.evaluation.scoring import score_case, summarize_results


def test_score_case_separates_tool_parameter_and_completion_failures() -> None:
    case = EvalCase(
        name="weather_parameter_case",
        category="weather",
        message="深圳天气",
        expected_tool="query_current_weather",
        expected_args={"city": "深圳"},
        required_final_substrings=("晴朗",),
    )
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "query_current_weather",
                    "args": {"city": "北京"},
                    "id": "call-weather",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps({"temperature_c": 30}),
            name="query_current_weather",
            tool_call_id="call-weather",
        ),
        AIMessage(content="天气数据已返回。"),
    ]

    result = score_case(case, messages)

    assert result.tool_selection_correct is True
    assert result.parameter_correct is False
    assert result.task_completed is False
    assert any("city" in failure for failure in result.failures)
    assert any("晴朗" in failure for failure in result.failures)


def test_score_route_requires_coordinates_from_endpoint_result() -> None:
    case = EvalCase(
        name="route_coordinate_case",
        category="route",
        message="规划驾车路线",
        expected_tool="resolve_route_endpoints",
        expected_tool_sequence=(
            "resolve_route_endpoints",
            "plan_driving_route",
        ),
        route_tool_from_endpoints="plan_driving_route",
    )
    endpoint_payload = {
        "origin": {"places": [{"latitude": 22.5, "longitude": 113.9}]},
        "destination": {
            "places": [{"latitude": 22.6, "longitude": 114.0}]
        },
    }
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "resolve_route_endpoints",
                    "args": {},
                    "id": "call-endpoints",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(endpoint_payload),
            name="resolve_route_endpoints",
            tool_call_id="call-endpoints",
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "plan_driving_route",
                    "args": {
                        "origin_latitude": 0,
                        "origin_longitude": 0,
                        "destination_latitude": 0,
                        "destination_longitude": 0,
                    },
                    "id": "call-route",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content="{}",
            name="plan_driving_route",
            tool_call_id="call-route",
        ),
        AIMessage(content="路线完成。"),
    ]

    result = score_case(case, messages)

    assert result.tool_selection_correct is True
    assert result.parameter_correct is False
    assert result.task_completed is False
    assert any("第一组候选坐标一致" in failure for failure in result.failures)


def test_summarize_results_excludes_no_tool_case_from_parameter_denominator() -> None:
    no_tool_case = EvalCase(
        name="no_tool_case",
        category="weather",
        message="现在天气怎么样？",
        expected_tool=None,
    )
    tool_case = EvalCase(
        name="tool_case",
        category="weather",
        message="深圳天气",
        expected_tool="query_current_weather",
        expected_args={"city": "深圳"},
    )
    results = [
        score_case(no_tool_case, [AIMessage(content="请告诉我城市。")]),
        score_case(tool_case, [AIMessage(content="没有调用工具。")]),
    ]

    metrics = summarize_results(results)

    assert metrics.total_cases == 2
    assert metrics.tool_selection_accuracy == 0.5
    assert metrics.parameter_cases == 1
    assert metrics.parameter_accuracy == 0
    assert metrics.task_completion_rate == 0.5


def test_external_tool_failure_blocks_instead_of_failing_completion() -> None:
    case = EvalCase(
        name="weather_provider_failure",
        category="weather",
        message="深圳天气",
        expected_tool="query_current_weather",
        expected_args={"city": "深圳"},
        required_final_substrings=("温度",),
    )
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "query_current_weather",
                    "args": {"city": "深圳"},
                    "id": "call-weather",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(
                {"ok": False, "error": "天气服务暂时不可用"}
            ),
            name="query_current_weather",
            tool_call_id="call-weather",
        ),
        AIMessage(content="天气服务暂时不可用，请稍后重试。"),
    ]

    result = score_case(case, messages)

    assert result.tool_selection_correct is True
    assert result.parameter_correct is True
    assert result.infrastructure_blocked is True
    assert result.task_completed is None
    assert result.failures == []
    assert result.infrastructure_errors == ["天气服务暂时不可用"]


def test_blocked_cases_are_excluded_from_completion_denominator() -> None:
    passing_case = EvalCase(
        name="no_tool_pass",
        category="weather",
        message="现在天气怎么样？",
        expected_tool=None,
    )
    blocked_result = score_case(
        EvalCase(
            name="blocked_weather",
            category="weather",
            message="深圳天气",
            expected_tool="query_current_weather",
            expected_args={"city": "深圳"},
        ),
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "query_current_weather",
                        "args": {"city": "深圳"},
                        "id": "call-weather",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(
                content=json.dumps(
                    {"ok": False, "error": "服务连接超时"}
                ),
                name="query_current_weather",
                tool_call_id="call-weather",
            ),
            AIMessage(content="服务连接超时，请稍后重试。"),
        ],
    )
    passing_result = score_case(
        passing_case,
        [AIMessage(content="请告诉我城市。")],
    )

    metrics = summarize_results([passing_result, blocked_result])

    assert metrics.scored_task_cases == 1
    assert metrics.infrastructure_blocked_cases == 1
    assert metrics.completed_cases == 1
    assert metrics.task_completion_rate == 1
