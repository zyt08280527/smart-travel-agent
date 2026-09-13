"""Deterministically re-rank the previous route after a preference change."""

import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, HumanMessage

from travel_agent.middleware.recommendation_explanation import (
    latest_planning_payload,
)
from travel_agent.presentation import render_planning_payload
from travel_agent.services.travel_replanning import replan_from_payload

_CANNOT_DRIVE_PATTERN = re.compile(
    r"(?:不能|不会|不想|不方便|没法).{0,4}(?:开车|驾车)|不开车"
)
_WALKING_LIMIT_PATTERNS = (
    re.compile(
        r"(?:最多|最大|只能)(?:能)?步行\s*(\d+(?:\.\d+)?)\s*(公里|千米|km|米|m)",
        re.IGNORECASE,
    ),
    re.compile(
        r"步行(?:距离)?(?:不超过|上限(?:是|为)?)\s*(\d+(?:\.\d+)?)\s*"
        r"(公里|千米|km|米|m)",
        re.IGNORECASE,
    ),
)
_TRANSFER_LIMIT_PATTERNS = (
    re.compile(r"(?:最多|不超过|只能)\s*换乘\s*([0-9零一二两三四五六七八九十]+)\s*次?"),
    re.compile(
        r"换乘(?:次数)?(?:最多|不超过|上限(?:是|为)?)\s*"
        r"([0-9零一二两三四五六七八九十]+)\s*次?"
    ),
)
_CHINESE_NUMBERS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _parse_nonnegative_int(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    return _CHINESE_NUMBERS.get(value)


def parse_preference_updates(content: str) -> dict[str, object]:
    """Extract supported deterministic preference changes from one follow-up."""
    updates: dict[str, object] = {}
    if _CANNOT_DRIVE_PATTERN.search(content):
        updates["can_drive"] = False

    for pattern in _WALKING_LIMIT_PATTERNS:
        match = pattern.search(content)
        if match is None:
            continue
        value = float(match.group(1))
        if match.group(2).lower() in {"公里", "千米", "km"}:
            value *= 1000
        updates["max_walking_distance_m"] = value
        break

    for pattern in _TRANSFER_LIMIT_PATTERNS:
        match = pattern.search(content)
        if match is None:
            continue
        value = _parse_nonnegative_int(match.group(1))
        if value is not None:
            updates["max_transfer_count"] = value
        break

    priority_patterns = (
        ("least_walking", ("优先少步行", "尽量少步行", "步行最少", "少走路")),
        ("fewest_transfers", ("优先少换乘", "尽量少换乘", "换乘最少")),
        ("cheapest", ("优先省钱", "费用优先", "价格优先", "最便宜", "最低费用")),
        ("fastest", ("优先速度", "时间优先", "优先最快", "越快越好", "最快")),
    )
    for priority, phrases in priority_patterns:
        if any(phrase in content for phrase in phrases):
            updates["priority"] = priority
            break
    return updates


class PreferenceReplanningMiddleware(AgentMiddleware):
    """Reuse prior route/weather facts when the user changes a hard constraint."""

    @hook_config(can_jump_to=["end"])
    def before_model(
        self,
        state: AgentState[Any],
        runtime: object,
    ) -> dict[str, Any] | None:
        del runtime
        messages = state.get("messages", [])
        if not messages or not isinstance(messages[-1], HumanMessage):
            return None
        content = messages[-1].content
        if not isinstance(content, str):
            return None
        preference_updates = parse_preference_updates(content)
        if not preference_updates:
            return None

        previous_payload = latest_planning_payload(list(messages[:-1]))
        if previous_payload is None:
            return None
        try:
            payload = replan_from_payload(
                previous_payload,
                preference_updates=preference_updates,
            )
        except ValueError as exc:
            return {
                "jump_to": "end",
                "messages": [
                    AIMessage(
                        content=(
                            "已继承上一轮行程并应用新的偏好或约束，但"
                            f"{exc}。请放宽约束后再试；本轮没有重新请求外部服务。"
                        )
                    )
                ],
            }

        rendered = render_planning_payload(payload)
        if rendered is None:
            return None
        answer = (
            "已继承上一轮的起终点、出发时间、天气和路线快照，"
            "并根据新的偏好或约束重新评分；本轮没有重新请求外部服务。\n\n"
            f"{rendered}"
        )
        previous_recommendation = previous_payload.get("recommendation")
        previous_mode = (
            previous_recommendation.get("recommended_mode")
            if isinstance(previous_recommendation, dict)
            else None
        )
        return {
            "jump_to": "end",
            "messages": [
                AIMessage(
                    content=answer,
                    additional_kwargs={
                        "travel_planning_payload": payload,
                        "travel_planning_update": {
                            "reused_previous_data": True,
                            "previous_recommended_mode": previous_mode,
                        },
                    },
                )
            ],
        }
