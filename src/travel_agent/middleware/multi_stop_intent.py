"""Prevent single-leg shortcuts from replacing explicitly multi-stop requests."""

import re

from langchain_core.messages import AIMessage, ToolMessage

_PATTERN = re.compile(
    r"多站点|多目的地|中间点|途经|依次(?:去|到|经过)|先(?:去|到).{1,120}(?:再|然后)(?:去|到)"
)
_SEQUENTIAL_VISITS = re.compile(
    r"从.{1,100}?出发.{0,20}?(?:到|去|前往).{1,120}?"
    r"(?:再|然后|接着|之后|随后)(?:去|到|前往)"
    r"|(?:到|去|前往).{1,100}?(?:停留|游玩|参观).{0,60}?"
    r"(?:再|然后|接着|之后|随后)(?:去|到|前往)"
)


def is_multi_stop_intent(content: str) -> bool:
    if _PATTERN.search(content):
        return True
    # A single A → B is still a single leg; three ordered places need all legs.
    if re.search(r"或者|还是|二选一|分别规划|两条路线", content):
        return False
    if _SEQUENTIAL_VISITS.search(content):
        return True
    return len([part for part in re.split(r"→|->|➡|➜", content) if part.strip()]) >= 3


def has_current_multi_stop(messages: list[object]) -> bool:
    for message in reversed(messages):
        if (isinstance(message, AIMessage)
                and "multi_stop_place_clarification" in message.additional_kwargs):
            return True
        if isinstance(message, AIMessage) and (
            "travel_planning_payload" in message.additional_kwargs
            or "transit_route_payload" in message.additional_kwargs
        ):
            return False
        if isinstance(message, ToolMessage):
            if message.name == "plan_multi_stop_trip":
                return True
            if message.name in {
                "recommend_travel_plan",
                "plan_transit_route",
                "plan_driving_route",
                "plan_walking_route",
            }:
                return False
    return False
