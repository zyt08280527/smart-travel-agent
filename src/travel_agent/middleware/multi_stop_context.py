"""Keep full multi-stop evidence in state while giving the model a compact view."""

import json
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from pydantic import ValidationError

from travel_agent.domain.multi_stop import MultiStopDraft, MultiStopLeg


def _leg_summary(leg: MultiStopLeg) -> dict:
    value = leg.model_dump(mode="json", exclude={
        "option": {"geometry"}, "route_details": True, "transit_details": True,
    })
    if leg.route_details is not None:
        value["duration_basis"] = leg.route_details.duration_basis
    if leg.transit_details is not None:
        value["transit_connections"] = [part.model_dump(mode="json", exclude={
            "geometry", "instruction",
        }) for part in leg.transit_details.legs if part.mode != "walking"]
    return value


def _compact_text(text: str) -> str:
    try:
        draft = MultiStopDraft.model_validate_json(text)
    except (ValidationError, ValueError):
        return text
    source = draft.result
    selection = source.selection.model_dump(mode="json", exclude={"plans"})
    selection["plans"] = []
    for plan in source.selection.plans:
        value = plan.model_dump(mode="json", exclude={"evaluation"})
        evaluation = plan.evaluation.model_dump(mode="json", exclude={"legs"})
        evaluation["legs"] = [{
            **timing.model_dump(mode="json", exclude={"leg"}),
            "leg": _leg_summary(timing.leg),
        } for timing in plan.evaluation.legs]
        value["evaluation"] = evaluation
        selection["plans"].append(value)
    summary = {
        "context_format": "multi_stop_model_summary_v1",
        "draft_id": str(draft.draft_id), "created_at": draft.created_at.isoformat(),
        "result": {
            "request": source.request.model_dump(mode="json"),
            "selection": selection,
            "segments": [{
                "origin_stop_id": segment.origin_stop_id,
                "destination_stop_id": segment.destination_stop_id,
                "candidates": [_leg_summary(leg) for leg in segment.candidates],
            } for segment in source.segments],
            "route_service_calls": source.route_service_calls,
        },
        "context_note": "这是模型用摘要。完整轨迹和导航步骤保留在可信草案及多站点页面，"
                        "不要补造缺失细节。生成草案不代表已经保存行程。",
    }
    return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))


def compact_multi_stop_context(request: ModelRequest) -> ModelRequest:
    messages = []
    changed = False
    for message in request.messages:
        if not isinstance(message, ToolMessage) or message.name != "plan_multi_stop_trip":
            messages.append(message)
            continue
        if isinstance(message.content, str):
            content = _compact_text(message.content)
        else:
            content = [
                {**block, "text": _compact_text(block["text"])}
                if isinstance(block, dict) and isinstance(block.get("text"), str) else block
                for block in message.content
            ]
        if content == message.content:
            messages.append(message)
        else:
            changed = True
            messages.append(message.model_copy(update={"content": content}))
    return request.override(messages=messages) if changed else request


class MultiStopContextMiddleware(AgentMiddleware):
    """Compact model input only; API cards, checkpoint and storage retain full data."""

    def wrap_model_call(self, request: ModelRequest,
                        handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        return handler(compact_multi_stop_context(request))

    async def awrap_model_call(self, request: ModelRequest,
                              handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
                              ) -> ModelResponse:
        return await handler(compact_multi_stop_context(request))
