import json
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tests.middleware.test_preference_replanning import direct_transit_payload
from tests.services.test_travel_replanning import build_payload
from travel_agent.api.runtime import AgentRuntime
from travel_agent.middleware.itinerary_save_grounding import _ground_from_planning
from travel_agent.middleware.preference_replanning import PreferenceReplanningMiddleware
from travel_agent.services.journey_versions import journey_versions


def test_versions_validate_deduplicate_and_do_not_mutate_sources():
    payload = build_payload()
    original = deepcopy(payload)
    messages = [
        ToolMessage(content=json.dumps(payload), name="recommend_travel_plan", tool_call_id="a"),
        AIMessage(content="", additional_kwargs={"travel_planning_payload": payload}),
        ToolMessage(content='{"ok":false}', name="recommend_travel_plan", tool_call_id="b"),
    ]
    versions = journey_versions(messages)
    assert len(versions) == 1
    assert versions[0]["version_id"] == journey_versions(messages)[0]["version_id"]
    versions[0]["payload"]["selected_mode"] = "walking"
    assert payload == original


def test_adoption_restores_selected_route_and_save_without_recalculating():
    old = build_payload()
    old["selected_mode"] = "walking"
    newer = build_payload()
    newer["selected_mode"] = "driving"
    messages = [AIMessage(content="", additional_kwargs={"travel_planning_payload": old}),
                AIMessage(content="", additional_kwargs={"travel_planning_payload": newer})]
    version_id = journey_versions(messages)[1]["version_id"]
    middleware = PreferenceReplanningMiddleware(
        clock=lambda: datetime.fromisoformat("2030-01-01T12:00:00+08:00"),
    )
    result = middleware.before_model({"messages": [*messages, HumanMessage(
        content=f"采用历史方案 {version_id}",
    )]}, None)
    adopted = result["messages"][0]
    payload = adopted.additional_kwargs["travel_planning_payload"]
    assert payload == old
    assert adopted.additional_kwargs["travel_planning_update"]["used_stale_snapshot"]
    saved = _ground_from_planning(payload, {}, manual_transit_selection=False)
    assert saved["travel_mode"] == "walking"
    assert newer["selected_mode"] == "driving"


def test_unknown_or_other_thread_version_never_falls_through_to_model():
    result = PreferenceReplanningMiddleware().before_model({"messages": [
        HumanMessage(content="采用历史方案 " + "a" * 64),
    ]}, None)
    assert result["jump_to"] == "end"
    assert "不存在于当前会话" in result["messages"][0].content
    assert not result["messages"][0].additional_kwargs


def test_direct_transit_adoption_preserves_manual_candidate():
    payload = direct_transit_payload()
    payload["selected_transit_candidate_index"] = 1
    messages = [AIMessage(content="", additional_kwargs={"transit_route_payload": payload})]
    version_id = journey_versions(messages)[0]["version_id"]
    result = PreferenceReplanningMiddleware().before_model({"messages": [
        *messages, HumanMessage(content=f"采用历史方案 {version_id}"),
    ]}, None)
    assert result["messages"][0].additional_kwargs["transit_route_payload"] == payload


@pytest.mark.asyncio
async def test_reading_versions_checks_conversation_and_does_not_write():
    runtime = AgentRuntime()
    runtime.get_conversation = AsyncMock()
    agent = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(values={
        "messages": [AIMessage(content="", additional_kwargs={
            "travel_planning_payload": build_payload(),
        })],
    })))
    runtime._agent = agent
    thread_id = uuid4()
    versions = await runtime.list_journey_versions(thread_id)
    runtime.get_conversation.assert_awaited_once_with(thread_id)
    assert len(versions) == 1
    assert versions[0]["card"]["type"] == "planning"
