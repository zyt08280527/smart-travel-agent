"""Runtime measurements and JSON reports for Agent evaluation runs."""

from datetime import datetime

from langchain.messages import AIMessage
from pydantic import BaseModel, Field

from travel_agent.evaluation.scoring import EvalCaseResult, EvalMetrics
from travel_agent.observability.http import ExternalHttpRequest


class EvalCaseMeasurement(BaseModel):
    """Behavior score plus observable runtime usage for one case."""

    result: EvalCaseResult
    latency_ms: float = Field(ge=0)
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    external_http_requests: list[ExternalHttpRequest] = Field(
        default_factory=list
    )


class EvalRunReport(BaseModel):
    """Comparable structured output for one complete dataset run."""

    report_version: int = 2
    dataset_schema_version: int
    dataset_sha256: str
    model_name: str
    started_at: datetime
    finished_at: datetime
    metrics: EvalMetrics
    total_latency_ms: float = Field(ge=0)
    average_case_latency_ms: float = Field(ge=0)
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    external_http_request_count: int = Field(ge=0)
    external_http_retry_count: int = Field(default=0, ge=0)
    external_http_failed_attempt_count: int = Field(default=0, ge=0)
    external_http_requests: list[ExternalHttpRequest]
    cases: list[EvalCaseMeasurement]


def measure_case(
    result: EvalCaseResult,
    messages: list[object],
    latency_ms: float,
) -> EvalCaseMeasurement:
    """Extract model and tool usage from one completed message trajectory."""
    ai_messages = [
        message for message in messages if isinstance(message, AIMessage)
    ]
    model_messages = [
        message
        for message in ai_messages
        if message.tool_calls
        or message.usage_metadata is not None
        or bool(message.response_metadata)
    ]
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    external_http_requests: list[ExternalHttpRequest] = []
    for message in ai_messages:
        usage = message.usage_metadata or {}
        input_tokens += int(usage.get("input_tokens", 0))
        output_tokens += int(usage.get("output_tokens", 0))
        total_tokens += int(usage.get("total_tokens", 0))
    for message in messages:
        artifact = getattr(message, "artifact", None)
        if not isinstance(artifact, dict):
            continue
        structured_content = artifact.get("structured_content")
        if not isinstance(structured_content, dict):
            continue
        observability = structured_content.get("observability")
        if not isinstance(observability, dict):
            continue
        raw_requests = observability.get("external_http_requests", [])
        if isinstance(raw_requests, list):
            external_http_requests.extend(
                ExternalHttpRequest.model_validate(request)
                for request in raw_requests
            )

    return EvalCaseMeasurement(
        result=result,
        latency_ms=latency_ms,
        model_calls=len(model_messages),
        tool_calls=sum(len(message.tool_calls) for message in ai_messages),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        external_http_requests=external_http_requests,
    )


def build_run_report(
    *,
    dataset_schema_version: int,
    dataset_sha256: str,
    model_name: str,
    started_at: datetime,
    finished_at: datetime,
    metrics: EvalMetrics,
    cases: list[EvalCaseMeasurement],
) -> EvalRunReport:
    """Aggregate measurements without inventing unavailable monetary cost."""
    if not cases:
        raise ValueError("评测案例测量结果不能为空")
    total_latency_ms = sum(case.latency_ms for case in cases)
    return EvalRunReport(
        dataset_schema_version=dataset_schema_version,
        dataset_sha256=dataset_sha256,
        model_name=model_name,
        started_at=started_at,
        finished_at=finished_at,
        metrics=metrics,
        total_latency_ms=total_latency_ms,
        average_case_latency_ms=total_latency_ms / len(cases),
        model_calls=sum(case.model_calls for case in cases),
        tool_calls=sum(case.tool_calls for case in cases),
        input_tokens=sum(case.input_tokens for case in cases),
        output_tokens=sum(case.output_tokens for case in cases),
        total_tokens=sum(case.total_tokens for case in cases),
        external_http_request_count=sum(
            len(case.external_http_requests) for case in cases
        ),
        external_http_retry_count=sum(
            request.attempt > 1
            for case in cases
            for request in case.external_http_requests
        ),
        external_http_failed_attempt_count=sum(
            not request.succeeded
            for case in cases
            for request in case.external_http_requests
        ),
        external_http_requests=[
            request
            for case in cases
            for request in case.external_http_requests
        ],
        cases=cases,
    )
