from copy import deepcopy

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from tests.services.test_multi_stop_selection import scenario
from travel_agent.domain.place import PlaceSearchResult
from travel_agent.middleware.itinerary_save_grounding import ItinerarySaveGroundingMiddleware
from travel_agent.middleware.multi_stop_intent import has_current_multi_stop
from travel_agent.middleware.multi_stop_place_clarification import (
    MultiStopPlaceClarificationMiddleware,
)


def fixture():
    request, _ = scenario()
    for index, stop in enumerate(request.stops):
        stop.point.longitude = 114 + index / 100
        stop.provider_place_id = f"poi-{index}"
    request.stops[1].name = "人民公园"
    search = PlaceSearchResult(query="人民公园", provider="amap", attribution="测试地点来源",
                              places=[{
                                  "display_name": f"人民公园，{address}", "city_code": "0755",
                                  "latitude": 22.5, "longitude": 114.01 + index / 25,
                                  "provider_place_id": f"poi-{index + 1}",
                              } for index, address in enumerate(["东门地址", "西门地址"])])
    return request, ToolMessage(name="search_places", tool_call_id="search",
                                content=search.model_dump_json())


def call(request):
    return AIMessage(id="model-call", content="", tool_calls=[{
        "name": "plan_multi_stop_trip", "id": "plan",
        "args": {"request": request.model_dump(mode="json")},
    }])


def run(messages):
    return MultiStopPlaceClarificationMiddleware().after_model({"messages": messages}, None)


def pending():
    request, search = fixture()
    message = run([HumanMessage(content="规划多站点"), search, call(request)])["messages"][0]
    return request, search, message


def test_ambiguous_search_blocks_route_call_and_preserves_request_and_message_identity():
    request, search = fixture()
    original = request.model_dump(mode="json")
    model = call(request)
    result = run([HumanMessage(content="规划多站点"), search, model])
    answer = result["messages"][0]
    assert result["jump_to"] == "end"
    assert answer.id == model.id
    assert answer.tool_calls == []
    assert "东门地址" in answer.content and "西门地址" in answer.content
    assert "测试地点来源" in answer.content
    assert answer.additional_kwargs["multi_stop_pending_request"] == original
    assert request.model_dump(mode="json") == original
    assert model.tool_calls


@pytest.mark.parametrize("reply", ["第2站选第1个", "选第一个", "人民公园，东门地址"])
def test_explicit_choice_of_displayed_candidate_can_continue(reply):
    request, search, question = pending()
    assert run([search, question, HumanMessage(content=reply), call(request)]) is None


@pytest.mark.parametrize("reply", ["好", "选第12个", "第3站选第1个", "第三站选第1个",
                                   "不要人民公园，东门地址", "第2站选第1个或者第2个"])
def test_vague_wrong_station_out_of_range_or_negative_replies_do_not_confirm(reply):
    request, search, question = pending()
    result = run([search, question, HumanMessage(content=reply), call(request)])
    assert result is not None
    assert result["messages"][0].tool_calls == []


def test_choice_must_match_actual_requested_coordinates_and_identity():
    request, search, question = pending()
    result = run([search, question, HumanMessage(content="第2站选第2个"), call(request)])
    assert result is not None  # Model incorrectly kept candidate one.


def test_explicit_second_choice_accepts_only_matching_poi():
    request, search, question = pending()
    request.stops[1].point.longitude = 114.05
    request.stops[1].provider_place_id = "poi-2"
    assert run([search, question, HumanMessage(content="第2站选第2个"), call(request)]) is None


def test_partial_confirmations_keep_other_visits_pending_and_preserve_each_choice():
    request, search = fixture()
    request.stops[-1].name = request.stops[1].name
    request.stops[-1].point = request.stops[1].point.model_copy()
    request.stops[-1].provider_place_id = request.stops[1].provider_place_id
    question = run([search, call(request)])["messages"][0]
    assert len(question.additional_kwargs["multi_stop_place_clarification"]) == 2
    history = [search, question, HumanMessage(content="第2站选第1个")]
    remaining = run([*history, call(request)])["messages"][0]
    assert [item["stop_index"] for item in
            remaining.additional_kwargs["multi_stop_place_clarification"]] == [2]
    request.stops[-1].point.longitude = 114.05
    request.stops[-1].provider_place_id = "poi-2"
    assert run([*history, remaining, HumanMessage(content="第3站选第2个"), call(request)]) is None


def test_address_scoped_to_one_visit_does_not_confirm_another_visit_to_same_poi():
    request, search = fixture()
    request.stops[-1].point = request.stops[1].point.model_copy()
    request.stops[-1].provider_place_id = request.stops[1].provider_place_id
    question = run([search, call(request)])["messages"][0]
    remaining = run([search, question, HumanMessage(content="第2站选人民公园，东门地址"),
                     call(request)])["messages"][0]
    assert [item["stop_index"] for item in
            remaining.additional_kwargs["multi_stop_place_clarification"]] == [2]


def test_later_choice_replaces_earlier_confirmation_instead_of_accepting_both():
    request, search, question = pending()
    history = [search, question, HumanMessage(content="第2站选第1个"),
               HumanMessage(content="第2站选第2个")]
    assert run([*history, call(request)]) is not None


@pytest.mark.parametrize("reply", [
    "第2站选第1个，不要保存。", "第2站选第1个，其他地点先不选。",
    "第2站选第1个。保留时间和不能驾车条件，不要规划或保存。",
])
def test_unrelated_negative_instruction_does_not_revoke_place_confirmation(reply):
    request, search, question = pending()
    assert run([search, question, HumanMessage(content=reply), call(request)]) is None


def natural_question(search):
    return run([search, AIMessage(id="natural", content=(
        "请确认第2站：人民公园\n"
        "1. 第1个候选：人民公园，东门地址（重要性最高）\n"
        "2. 第2个候选：人民公园，西门地址\n请按站编号确认。"))])["messages"][0]


def test_model_generated_candidate_numbers_are_recorded_before_any_planning_call():
    request, search = fixture()
    question = natural_question(search)
    assert question.additional_kwargs["multi_stop_place_clarification"][0]["stop_id"] == "station:1"
    assert "重要性最高" not in question.content
    assert run([search, question, HumanMessage(content="第2站选第1个，不要保存"),
                call(request)]) is None


def test_partial_or_invented_candidate_display_does_not_compress_indices():
    _, search = fixture()
    model = AIMessage(content="请确认第2站\n1. 人民公园，东门地址\n2. 人民公园，编造地址")
    assert run([search, model]) is None


def test_final_answer_after_planning_is_not_reinterpreted_as_place_confirmation():
    _, search = fixture()
    final = AIMessage(content="第2站\n1. 人民公园，东门地址\n2. 人民公园，西门地址\n请确认保存")
    result = ToolMessage(name="plan_multi_stop_trip", tool_call_id="plan", content='{}')
    assert run([HumanMessage(content="规划"), search, result, final]) is None


def test_repeated_natural_clarification_keeps_already_confirmed_other_station():
    request, search = fixture()
    first = natural_question(search)
    # A later clarification lists a different station, so station 2 stays confirmed.
    second = run([search, AIMessage(content="请确认第3站\n1. 人民公园，东门地址\n"
                                           "2. 人民公园，西门地址")])["messages"][0]
    assert run([search, first, HumanMessage(content="第2站选第1个，其他先不选"),
                second, HumanMessage(content="第3站选第1个，不要保存"), call(request)]) is None


def test_unique_exact_name_does_not_require_confirmation_of_unrelated_search_hits():
    request, search = fixture()
    payload = PlaceSearchResult.model_validate_json(search.content)
    payload.places[1].display_name = "人民公园停车场，西门地址"
    search.content = payload.model_dump_json()
    assert run([search, call(request)]) is None


def test_unrelated_tools_and_direct_coordinates_are_not_changed():
    request, _ = fixture()
    assert run([call(request)]) is None
    model = call(request)
    model.tool_calls[0]["name"] = "search_places"
    assert run([model]) is None


@pytest.mark.parametrize("field,value", [("city_code", "440100"),
                                        ("provider_place_id", "invented")])
def test_rewritten_search_city_or_poi_identity_cannot_bypass_clarification(field, value):
    request, search = fixture()
    payload = PlaceSearchResult.model_validate_json(search.content)
    setattr(payload.places[0], field, value)
    search.content = payload.model_dump_json()
    result = run([search, call(request)])
    assert result["messages"][0].tool_calls == []
    assert "与搜索结果不一致" in result["messages"][0].content


def test_resume_context_is_only_in_model_input_and_keeps_complete_constraints():
    _, _, question = pending()
    messages = [question, HumanMessage(content="第2站选第1个")]
    original = deepcopy(messages)
    request = ModelRequest(model=FakeMessagesListChatModel(responses=[]), messages=messages)
    captured = []
    MultiStopPlaceClarificationMiddleware().wrap_model_call(
        request, lambda value: captured.append(value))
    assert request.messages == original
    context = captured[0].messages[-2].content
    assert '"stop_id"' in context and '"arrival_deadline"' in context
    assert captured[0].messages[-1].content == "第2站选第1个"


@pytest.mark.asyncio
async def test_async_model_wrapper_receives_same_resume_context():
    _, _, question = pending()
    request = ModelRequest(model=FakeMessagesListChatModel(responses=[]),
                           messages=[question, HumanMessage(content="选第一个")])
    captured = []

    async def handler(value):
        captured.append(value)

    await MultiStopPlaceClarificationMiddleware().awrap_model_call(request, handler)
    assert len(captured[0].messages) == 3


class ToolCallingModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.mark.asyncio
async def test_real_agent_graph_does_not_execute_ambiguous_planning_tool():
    request, search = fixture()
    invoked = []

    @tool
    def plan_multi_stop_trip(request: dict) -> str:
        """Record a planning call during the test."""
        invoked.append(request)
        return '{}'

    graph = create_agent(model=ToolCallingModel(responses=[call(request)]),
                         tools=[plan_multi_stop_trip],
                         middleware=[MultiStopPlaceClarificationMiddleware()])
    result = await graph.ainvoke({"messages": [HumanMessage(content="规划多站点"), search]})
    assert invoked == []
    assert "请先确认" in result["messages"][-1].content
    assert result["messages"][-1].tool_calls == []


@pytest.mark.asyncio
async def test_checkpoint_resume_executes_once_after_user_confirms_and_keeps_request():
    request, search = fixture()
    invoked = []

    @tool
    def plan_multi_stop_trip(request: dict) -> str:
        """Record a confirmed multi-stop planning request."""
        invoked.append(request)
        return '{}'

    second_call = call(request).model_copy(update={"id": "second-call"})
    graph = create_agent(
        model=ToolCallingModel(responses=[call(request), second_call, AIMessage(content="完成")]),
        tools=[plan_multi_stop_trip], middleware=[MultiStopPlaceClarificationMiddleware()],
        checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "clarify-places"}}
    await graph.ainvoke({"messages": [HumanMessage(content="规划多站点"), search]}, config)
    assert invoked == []
    result = await graph.ainvoke({"messages": [HumanMessage(content="第2站选第1个")]}, config)
    assert invoked == [request.model_dump(mode="json")]
    assert result["messages"][-1].content == "完成"


def test_pending_clarification_blocks_older_single_snapshot_and_save():
    _, _, question = pending()
    old = AIMessage(content="", additional_kwargs={"travel_planning_payload": {"old": True}})
    assert has_current_multi_stop([old, question])
    save = AIMessage(content="", tool_calls=[{
        "name": "save_itinerary", "id": "save", "args": {},
    }])
    result = ItinerarySaveGroundingMiddleware().after_model(
        {"messages": [old, question, save]}, None)
    assert result["messages"][0].tool_calls == []
    assert "不能用单段保存替代" in result["messages"][0].content
def test_missing_time_does_not_bypass_ambiguous_place_confirmation():
    request, search = fixture()
    request.departure_time = None
    request.arrival_deadline = None
    for stop in request.stops:
        stop.earliest_arrival_at = stop.latest_arrival_at = None
    result = run([HumanMessage(content="A → 人民公园 → C"), search, call(request)])
    assert result["messages"][0].tool_calls == []
    assert "东门地址" in result["messages"][0].content
    assert "西门地址" in result["messages"][0].content
    preserved = result["messages"][0].additional_kwargs["multi_stop_pending_request"]
    assert preserved["departure_time"] is None
