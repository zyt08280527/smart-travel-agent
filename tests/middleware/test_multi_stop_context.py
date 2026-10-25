import json
from copy import deepcopy

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage

from tests.middleware.test_multi_stop_explanation import draft_message
from travel_agent.domain.multi_stop import MultiStopDraft
from travel_agent.domain.transit import TransitOption
from travel_agent.middleware.multi_stop_context import (
    MultiStopContextMiddleware,
    compact_multi_stop_context,
)


def request_for(message):
    return ModelRequest(model=FakeMessagesListChatModel(responses=[]), messages=[message])


def large_draft():
    message = draft_message()
    draft = MultiStopDraft.model_validate_json(message.content)
    for segment in draft.result.segments:
        for leg in segment.candidates:
            leg.option.geometry = [draft.result.request.stops[0].point] * 1000
    for plan in draft.result.selection.plans:
        for timing in plan.evaluation.legs:
            timing.leg.option.geometry = [draft.result.request.stops[0].point] * 1000
    message.content = draft.model_dump_json()
    return message, draft


@pytest.mark.parametrize("blocks", [False, True])
def test_compaction_keeps_metrics_and_trusted_state_but_removes_geometry(blocks):
    message, draft = large_draft()
    original = deepcopy(message)
    if blocks:
        message.content = [{"type": "text", "text": message.content}]
    request = request_for(message)
    compacted = compact_multi_stop_context(request)
    value = compacted.messages[0]
    text = value.content[0]["text"] if blocks else value.content
    summary = json.loads(text)
    assert len(text) < len(original.content) / 5
    assert '"geometry"' not in text
    assert summary["result"]["request"] == draft.result.request.model_dump(mode="json")
    selection = summary["result"]["selection"]
    assert selection["recommended_plan_id"] == draft.result.selection.recommended_plan_id
    assert selection["selected_plan_id"] == draft.result.selection.selected_plan_id
    for result, plan in zip(selection["plans"], draft.result.selection.plans, strict=True):
        assert result["scores"] == plan.scores.model_dump()
        assert result["evaluation"]["totals"] == plan.evaluation.totals.model_dump()
        assert result["evaluation"]["stops"] == [s.model_dump(mode="json")
                                                  for s in plan.evaluation.stops]
    assert value.tool_call_id == original.tool_call_id
    assert value.id == original.id
    raw = message.content[0]["text"] if blocks else message.content
    assert raw == original.content
    assert MultiStopDraft.model_validate_json(raw).result.segments[0].candidates[0].option.geometry


@pytest.mark.parametrize("content", ['{"ok":false,"error":"查询失败"}', 'not json'])
def test_errors_are_not_rewritten_or_presented_as_success(content):
    message = ToolMessage(content=content, name="plan_multi_stop_trip", tool_call_id="failed")
    request = request_for(message)
    assert compact_multi_stop_context(request) is request


def test_other_tool_results_are_not_changed():
    message = ToolMessage(content='{"geometry":[]}', name="search_places", tool_call_id="search")
    request = request_for(message)
    assert compact_multi_stop_context(request) is request


@pytest.mark.asyncio
async def test_both_model_wrappers_use_compact_input_only():
    message, _ = large_draft()
    request = request_for(message)
    captured = []
    middleware = MultiStopContextMiddleware()
    middleware.wrap_model_call(request, lambda value: captured.append(value))

    async def handler(value):
        captured.append(value)

    await middleware.awrap_model_call(request, handler)
    assert len(captured) == 2
    assert all('"geometry"' not in value.messages[0].content for value in captured)
    assert '"geometry"' in request.messages[0].content


def test_unknown_cost_failure_and_transit_connection_facts_survive_compaction():
    message, draft = large_draft()
    draft.result.selection.plans[0].evaluation.totals.total_cost_yuan = None
    leg = draft.result.segments[0].candidates[0]
    leg.option.status = "failed"
    leg.option.failure_reason = "本次路线查询失败"
    transit = draft.result.segments[0].candidates[1]
    transit.transit_details = TransitOption(
        distance_m=1000, duration_s=600, walking_distance_m=100, transfer_count=0,
        legs=[{"mode": "subway", "distance_m": 900, "line_name": "1号线",
               "departure_stop": "起始站", "arrival_stop": "到达站",
               "geometry": [draft.result.request.stops[0].point]},
              {"mode": "walking", "distance_m": 100, "instruction": "详细导航文本"}],
    )
    message.content = draft.model_dump_json()
    summary = json.loads(compact_multi_stop_context(request_for(message)).messages[0].content)
    totals = summary["result"]["selection"]["plans"][0]["evaluation"]["totals"]
    assert totals["total_cost_yuan"] is None
    candidates = summary["result"]["segments"][0]["candidates"]
    assert candidates[0]["option"]["failure_reason"] == "本次路线查询失败"
    connection = candidates[1]["transit_connections"][0]
    assert connection["line_name"] == "1号线"
    assert connection["departure_stop"] == "起始站"
    assert connection["arrival_stop"] == "到达站"
    assert "geometry" not in connection


class CapturingModel(FakeMessagesListChatModel):
    seen: list = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen = deepcopy(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.mark.asyncio
async def test_agent_graph_uses_summary_but_returns_full_tool_snapshot():
    message, _ = large_draft()
    model = CapturingModel(responses=[AIMessage(content="已读取摘要")])
    graph = create_agent(model=model, tools=[], middleware=[MultiStopContextMiddleware()])
    result = await graph.ainvoke({"messages": [message]})
    viewed = next(item for item in model.seen if isinstance(item, ToolMessage))
    stored = next(item for item in result["messages"] if isinstance(item, ToolMessage))
    assert '"geometry"' not in viewed.content
    assert stored.content == message.content
    assert '"geometry"' in stored.content
