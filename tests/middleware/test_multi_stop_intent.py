import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import HumanMessage

from travel_agent.middleware.multi_stop_intent import is_multi_stop_intent
from travel_agent.middleware.multi_stop_place_clarification import (
    MultiStopPlaceClarificationMiddleware,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("深圳大学粤海校区 → 深圳人才公园，停留60分钟 → 深圳湾公园", True),
        ("A -> B -> C", True),
        ("A → B", False),
        ("A → B 或者 C → D", False),
        ("依次去学校、公园、酒店", True),
        (
            "从深圳大学粤海校区出发，到深圳人才公园停留60分钟，再去深圳湾公园。不能驾车，暂时不指定出发时间。",
            True,
        ),
        ("从学校出发，到公园，然后去酒店", True),
        ("到人才公园停留一小时，接着去深圳湾公园", True),
        ("到公园停留一小时再去酒店，或者只去公园", False),
        ("从学校出发，到公园，不能驾车", False),
        ("到公园停留60分钟后再告诉我路线", False),
    ],
)
def test_ordered_chain_is_multi_stop(text, expected):
    assert is_multi_stop_intent(text) is expected


@pytest.mark.parametrize(
    ("text", "multi"),
    [
        ("A → B → C", True),
        ("A → B", False),
        (
            "从深圳大学粤海校区出发，到深圳人才公园停留60分钟，再去深圳湾公园。不能驾车，暂时不指定出发时间。",
            True,
        ),
    ],
)
def test_model_cannot_choose_single_leg_tool_for_explicit_multi_stop(text, multi):
    names = [
        "search_places",
        "plan_multi_stop_trip",
        "recommend_travel_plan",
        "plan_transit_route",
        "plan_driving_route",
        "plan_walking_route",
    ]
    request = ModelRequest(
        model=FakeMessagesListChatModel(responses=[]),
        messages=[HumanMessage(content=text)],
        tools=[{"name": name, "description": name, "parameters": {}} for name in names],
    )
    captured = []
    MultiStopPlaceClarificationMiddleware().wrap_model_call(
        request, lambda value: captured.append(value)
    )
    assert [tool["name"] for tool in captured[0].tools] == (names[:2] if multi else names)
    assert len(request.tools) == 6
