from datetime import UTC, datetime, timedelta

from langchain.messages import AIMessage, ToolMessage

from travel_agent.evaluation.reporting import build_run_report, measure_case
from travel_agent.evaluation.scoring import EvalCaseResult, summarize_results
from travel_agent.observability.http import ExternalHttpRequest


def test_measure_case_sums_usage_across_multiple_model_rounds() -> None:
    result = EvalCaseResult(
        name="usage_case",
        category="weather",
        tool_selection_correct=True,
        parameter_correct=True,
        task_completed=True,
        actual_tool_sequence=["query_current_weather"],
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
            usage_metadata={
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
            },
        ),
        ToolMessage(
            content="天气结果",
            name="query_current_weather",
            tool_call_id="call-weather",
            artifact={
                "structured_content": {
                    "observability": {
                        "external_http_requests": [
                            ExternalHttpRequest(
                                provider="open-meteo-weather",
                                method="GET",
                                host="api.open-meteo.com",
                                path="/v1/forecast",
                                status_code=200,
                                latency_ms=25,
                                succeeded=True,
                            ).model_dump()
                        ]
                    }
                }
            },
        ),
        AIMessage(
            content="深圳天气晴朗。",
            usage_metadata={
                "input_tokens": 140,
                "output_tokens": 10,
                "total_tokens": 150,
            },
        ),
    ]

    measurement = measure_case(result, messages, latency_ms=250.5)

    assert measurement.model_calls == 2
    assert measurement.tool_calls == 1
    assert measurement.input_tokens == 240
    assert measurement.output_tokens == 30
    assert measurement.total_tokens == 270
    assert measurement.latency_ms == 250.5
    assert len(measurement.external_http_requests) == 1
    assert measurement.external_http_requests[0].provider == (
        "open-meteo-weather"
    )


def test_build_report_aggregates_case_measurements() -> None:
    case_result = EvalCaseResult(
        name="complete_case",
        category="weather",
        tool_selection_correct=True,
        parameter_correct=True,
        task_completed=True,
        actual_tool_sequence=["query_current_weather"],
    )
    first = measure_case(
        case_result,
        [AIMessage(content="完成")],
        latency_ms=100,
    )
    observation = ExternalHttpRequest(
        provider="open-meteo-weather",
        method="GET",
        host="api.open-meteo.com",
        path="/v1/forecast",
        status_code=200,
        latency_ms=20,
        succeeded=True,
    )
    first = first.model_copy(
        update={"external_http_requests": [observation]}
    )
    retry_observation = observation.model_copy(
        update={"attempt": 2, "status_code": 503, "succeeded": False}
    )
    second = first.model_copy(
        update={
            "latency_ms": 300,
            "external_http_requests": [retry_observation],
        }
    )
    metrics = summarize_results([case_result, case_result])
    started_at = datetime(2026, 7, 29, tzinfo=UTC)

    report = build_run_report(
        dataset_schema_version=1,
        dataset_sha256="a" * 64,
        model_name="qwen-plus",
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=1),
        metrics=metrics,
        cases=[first, second],
    )

    assert report.total_latency_ms == 400
    assert report.average_case_latency_ms == 200
    assert report.model_calls == 2
    assert report.external_http_request_count == 2
    assert report.external_http_retry_count == 1
    assert report.external_http_failed_attempt_count == 1
    assert report.metrics.task_completion_rate == 1
