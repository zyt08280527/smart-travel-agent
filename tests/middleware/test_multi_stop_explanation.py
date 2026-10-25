from uuid import uuid4

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tests.services.test_multi_stop_selection import scenario
from travel_agent.domain.multi_stop import MultiStopDraft, MultiStopPlanningResult
from travel_agent.middleware.recommendation_explanation import RecommendationExplanationMiddleware
from travel_agent.services.multi_stop_selection import select_multi_stop_plans


def draft_message():
    request, groups = scenario()
    request.preferences.can_drive = False
    selection = select_multi_stop_plans(request, groups)
    selection.selected_plan_id = selection.plans[-1].plan_id
    draft = MultiStopDraft(
        draft_id=uuid4(), created_at=request.departure_time.departure_at,
        result=MultiStopPlanningResult(request=request, segments=groups,
                                      selection=selection, route_service_calls=6),
    )
    return ToolMessage(name="plan_multi_stop_trip", tool_call_id="multi",
                       content=draft.model_dump_json())


def explain(messages, question="为什么推荐这个完整行程？"):
    return RecommendationExplanationMiddleware().before_model(
        {"messages": [*messages, HumanMessage(content=question)]}, object())


def test_explains_system_recommendation_and_non_driving_without_replacing_selection():
    message = draft_message()
    original = message.content
    result = explain([message])
    assert result["jump_to"] == "end"
    text = result["messages"][0].content
    assert "公共交通 → 公共交通" in text
    assert "不代表只能步行" in text
    assert "当前选择与系统推荐不同" in text
    assert "不保证同时最快、最便宜" in text
    assert "未计算天气" in text
    assert message.content == original


@pytest.mark.parametrize("payload", ['{}', '{"ok":false}', 'not json'])
def test_latest_failed_result_cannot_resurrect_older_success(payload):
    result = explain([draft_message(), ToolMessage(name="plan_multi_stop_trip",
                     tool_call_id="failed", content=payload)])
    assert "不足以解释" in result["messages"][0].content
    assert "推荐各段" not in result["messages"][0].content


@pytest.mark.parametrize("question", [
    "为什么推荐这个，停留改为60分钟重新规划",
    "为什么推荐这个，只改停留时间",
    "为什么推荐这个，增加一个中间点",
])
def test_explanation_with_requested_change_continues_to_planner(question):
    assert explain([draft_message()], question) is None


@pytest.mark.parametrize("status", ["infeasible", "insufficient_data"])
def test_no_feasible_result_does_not_claim_recommendation(status):
    message = draft_message()
    draft = MultiStopDraft.model_validate_json(message.content)
    draft.result.selection.status = status
    message.content = draft.model_dump_json()
    assert "没有可确认可行" in explain([message])["messages"][0].content


def test_unknown_cost_does_not_become_free():
    message = draft_message()
    draft = MultiStopDraft.model_validate_json(message.content)
    winner = next(plan for plan in draft.result.selection.plans
                  if plan.plan_id == draft.result.selection.recommended_plan_id)
    winner.evaluation.totals.total_cost_yuan = None
    message.content = draft.model_dump_json()
    assert "总费用未知" in explain([message])["messages"][0].content


@pytest.mark.asyncio
async def test_multi_stop_explanation_bypasses_model_in_graph():
    agent = create_agent(FakeMessagesListChatModel(responses=[AIMessage(content="不应调用")]),
                         tools=[], middleware=[RecommendationExplanationMiddleware()])
    result = await agent.ainvoke({"messages": [draft_message(),
                                 HumanMessage(content="为什么推荐这个完整行程？")]})
    assert "不代表只能步行" in result["messages"][-1].content
    assert "不应调用" not in result["messages"][-1].content
