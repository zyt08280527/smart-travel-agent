"""Deterministically re-rank the previous route after a preference change."""

import json
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from travel_agent.domain.decision import (
    JourneyContext,
    TravelComparisonResult,
    TravelMode,
)
from travel_agent.domain.transit import TransitPlan
from travel_agent.middleware.recommendation_explanation import (
    latest_planning_payload,
)
from travel_agent.presentation import render_planning_payload
from travel_agent.services.travel_replanning import (
    build_route_refresh_args,
    is_route_snapshot_fresh,
    replan_from_payload,
    replan_from_stale_payload,
)

Clock = Callable[[], datetime]

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
_TRANSIT_CANDIDATE_PATTERN = re.compile(
    r"(?:选择|改选|切换到?)\s*(?:公共交通)?候选\s*(\d+)"
)
_MODE_SELECTION_PATTERN = re.compile(
    r"(?:选择|改选|切换到?)\s*(驾车|开车|公共交通|公交|地铁|步行)"
    r"(?:方案|方式|行程)?"
)
_MODE_SELECTIONS = {
    "驾车": "driving",
    "开车": "driving",
    "公共交通": "transit",
    "公交": "transit",
    "地铁": "transit",
    "步行": "walking",
}
_MODE_NAMES = {
    "driving": "驾车",
    "transit": "公共交通",
    "walking": "步行",
}
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


def _tool_failed(message: ToolMessage) -> bool:
    blocks = message.content if isinstance(message.content, list) else [message.content]
    for block in blocks:
        text = block.get("text") if isinstance(block, dict) else block
        if not isinstance(text, str):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("ok") is False:
            return True
    return False


def _message_json_payloads(message: ToolMessage) -> list[dict[str, object]]:
    blocks = message.content if isinstance(message.content, list) else [message.content]
    payloads: list[dict[str, object]] = []
    for block in blocks:
        text = block.get("text") if isinstance(block, dict) else block
        if not isinstance(text, str):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("ok") is not False:
            payloads.append(payload)
    return payloads


def _latest_route_choice_payload(
    messages: list[object],
) -> tuple[str, dict[str, object]] | None:
    """Return the newest selectable comprehensive or transit-only snapshot."""
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            planning = message.additional_kwargs.get("travel_planning_payload")
            if isinstance(planning, dict):
                return "planning", planning
            transit = message.additional_kwargs.get("transit_route_payload")
            if isinstance(transit, dict):
                return "transit", transit
        if not isinstance(message, ToolMessage):
            continue
        payloads = _message_json_payloads(message)
        if not payloads:
            continue
        if message.name == "recommend_travel_plan":
            return "planning", payloads[-1]
        if message.name == "plan_transit_route":
            return "transit", payloads[-1]
    return None


def _select_direct_transit_candidate(
    payload: dict[str, object],
    candidate_index: int,
) -> tuple[dict[str, object], str]:
    plan = TransitPlan.model_validate(payload)
    if not 0 <= candidate_index < len(plan.options):
        raise ValueError("指定的公共交通候选不存在")
    selected = plan.options[candidate_index]
    line_names = list(
        dict.fromkeys(leg.line_name for leg in selected.legs if leg.line_name)
    )
    updated_payload = plan.model_dump(mode="json")
    updated_payload["selected_transit_candidate_index"] = candidate_index
    cost_text = (
        f"{selected.cost_yuan:g} 元"
        if selected.cost_yuan is not None
        else "费用待确认"
    )
    lines_text = " → ".join(line_names) if line_names else "线路名称未提供"
    answer = (
        f"已选择公共交通候选 {candidate_index + 1}：{lines_text}；"
        f"约 {round(selected.duration_s / 60)} 分钟，"
        f"步行 {round(selected.walking_distance_m)} 米，"
        f"换乘 {selected.transfer_count} 次，{cost_text}。"
        "本轮复用上一轮路线快照，没有重新请求外部服务；"
        "出发前请通过实时导航确认班次与运营状态。"
    )
    return updated_payload, answer


def _refresh_request_metadata(
    messages: list[object],
    tool_call_id: str,
) -> tuple[dict[str, object], dict[str, object], object] | None:
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        if not any(call.get("id") == tool_call_id for call in message.tool_calls):
            continue
        payload = message.additional_kwargs.get("travel_refresh_fallback_payload")
        updates = message.additional_kwargs.get("travel_refresh_preference_updates")
        if not isinstance(payload, dict) or not isinstance(updates, dict):
            return None
        return (
            payload,
            updates,
            message.additional_kwargs.get("travel_refresh_previous_mode"),
        )
    return None


def _parse_nonnegative_int(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    return _CHINESE_NUMBERS.get(value)


def parse_transit_candidate_selection(content: str) -> int | None:
    """Return a zero-based concrete public-transit candidate index."""
    match = _TRANSIT_CANDIDATE_PATTERN.search(content)
    if match is None:
        return None
    candidate_number = int(match.group(1))
    return candidate_number - 1 if candidate_number > 0 else None


def parse_mode_selection(content: str) -> TravelMode | None:
    """Return the explicitly confirmed top-level travel mode."""
    match = _MODE_SELECTION_PATTERN.search(content)
    if match is None:
        return None
    selected = _MODE_SELECTIONS.get(match.group(1))
    return selected if selected in {"driving", "transit", "walking"} else None


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

    if "地铁优先" in content or "优先地铁" in content:
        updates["transit_strategy"] = "subway_first"
    elif re.search(r"(?:公共交通|公交|地铁).{0,8}(?:少换乘|换乘最少)", content):
        updates["transit_strategy"] = "fewest_transfers"
    elif re.search(r"(?:公共交通|公交|地铁).{0,8}(?:少步行|步行最少)", content):
        updates["transit_strategy"] = "least_walking"
    elif any(
        phrase in content
        for phrase in ("公共交通综合推荐", "公交综合推荐", "恢复公共交通默认")
    ):
        updates["transit_strategy"] = "recommended"

    priority_patterns = (
        ("balanced", ("均衡考虑", "均衡优先", "恢复均衡")),
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

    def __init__(
        self,
        timezone: str = "Asia/Shanghai",
        *,
        clock: Clock | None = None,
    ) -> None:
        self._zone = ZoneInfo(timezone)
        self._clock = clock or (lambda: datetime.now(self._zone))

    @hook_config(can_jump_to=["end", "tools"])
    def before_model(
        self,
        state: AgentState[Any],
        runtime: object,
    ) -> dict[str, Any] | None:
        del runtime
        messages = state.get("messages", [])
        if not isinstance(messages, list):
            return None
        if messages and isinstance(messages[-1], ToolMessage):
            failed_tool = messages[-1]
            if (
                failed_tool.name == "recommend_travel_plan"
                and failed_tool.tool_call_id.startswith("refresh-plan-")
            ):
                metadata = _refresh_request_metadata(
                    list(messages[:-1]),
                    failed_tool.tool_call_id,
                )
                if metadata is not None:
                    previous_payload, preference_updates, previous_mode = metadata
                    if not _tool_failed(failed_tool):
                        payloads = _message_json_payloads(failed_tool)
                        if payloads:
                            payload = payloads[-1]
                            selected_mode = previous_payload.get("selected_mode")
                            if selected_mode in {"driving", "walking", "transit"}:
                                payload["selected_mode"] = selected_mode
                            try:
                                payload = TravelComparisonResult.model_validate(
                                    payload
                                ).model_dump(mode="json")
                            except ValueError:
                                return None
                            rendered = render_planning_payload(payload)
                            if rendered is None:
                                return None
                            return {
                                "jump_to": "end",
                                "messages": [
                                    AIMessage(
                                        content=(
                                            "已刷新路线并应用新的方案偏好。\n\n"
                                            f"{rendered}"
                                        ),
                                        additional_kwargs={
                                            "travel_planning_payload": payload,
                                            "travel_planning_update": {
                                                "reused_previous_data": False,
                                                "route_refreshed": True,
                                                "previous_recommended_mode": previous_mode,
                                            },
                                        },
                                    )
                                ],
                            }
                    if "transit_strategy" in preference_updates:
                        return {
                            "jump_to": "end",
                            "messages": [
                                AIMessage(
                                    content=(
                                        "公共交通选线策略需要重新查询路线，但本次"
                                        "刷新失败。已保留上一轮路线，请稍后重试。"
                                    )
                                )
                            ],
                        }
                    try:
                        payload = replan_from_stale_payload(
                            previous_payload,
                            preference_updates=preference_updates,
                        )
                    except ValueError as exc:
                        return {
                            "jump_to": "end",
                            "messages": [
                                AIMessage(
                                    content=(
                                        "路线自动刷新失败，并且旧路线快照"
                                        f"{exc}。请稍后重新规划或放宽约束。"
                                    )
                                )
                            ],
                        }
                    rendered = render_planning_payload(payload)
                    if rendered is None:
                        return None
                    return {
                        "jump_to": "end",
                        "messages": [
                            AIMessage(
                                content=(
                                    "路线自动刷新失败，以下内容使用已过期的"
                                    "上一轮路线快照重新评分，仅供临时参考。\n\n"
                                    f"{rendered}"
                                ),
                                additional_kwargs={
                                    "travel_planning_payload": payload,
                                    "travel_planning_update": {
                                        "reused_previous_data": True,
                                        "used_stale_snapshot": True,
                                        "refresh_failed": True,
                                        "previous_recommended_mode": previous_mode,
                                    },
                                },
                            )
                        ],
                    }
        if not messages or not isinstance(messages[-1], HumanMessage):
            return None
        content = messages[-1].content
        if not isinstance(content, str):
            return None
        transit_candidate_index = parse_transit_candidate_selection(content)
        if transit_candidate_index is not None:
            route_choice = _latest_route_choice_payload(list(messages[:-1]))
            if route_choice is None:
                return None
            choice_type, previous_payload = route_choice
            if choice_type == "transit":
                try:
                    payload, answer = _select_direct_transit_candidate(
                        previous_payload,
                        transit_candidate_index,
                    )
                except ValueError as exc:
                    return {
                        "jump_to": "end",
                        "messages": [AIMessage(content=f"无法选择该路线：{exc}。")],
                    }
                return {
                    "jump_to": "end",
                    "messages": [
                        AIMessage(
                            content=answer,
                            additional_kwargs={"transit_route_payload": payload},
                        )
                    ],
                }
            context_value = previous_payload.get("context")
            if not isinstance(context_value, dict):
                return None
            try:
                context = JourneyContext.model_validate(context_value)
                if not is_route_snapshot_fresh(
                    context,
                    reference_at=self._clock(),
                ):
                    return {
                        "jump_to": "end",
                        "messages": [
                            AIMessage(
                                content=(
                                    "这组公共交通候选已经过期，请先选择公共交通"
                                    "策略刷新路线，再选择具体候选。"
                                )
                            )
                        ],
                    }
                payload = replan_from_payload(
                    previous_payload,
                    preference_updates={},
                    transit_candidate_index=transit_candidate_index,
                    selected_mode="transit",
                )
            except ValueError as exc:
                return {
                    "jump_to": "end",
                    "messages": [AIMessage(content=f"无法选择该路线：{exc}。")],
                }
            rendered = render_planning_payload(payload)
            if rendered is None:
                return None
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
                        content=(
                            f"已选择公共交通候选 {transit_candidate_index + 1}，"
                            "并基于该路线更新出行比较；本轮没有重新请求外部服务。"
                            f"\n\n{rendered}"
                        ),
                        additional_kwargs={
                            "travel_planning_payload": payload,
                            "travel_planning_update": {
                                "reused_previous_data": True,
                                "manual_transit_selection": True,
                                "previous_recommended_mode": previous_mode,
                            },
                        },
                    )
                ],
                }
        selected_mode = parse_mode_selection(content)
        if selected_mode is not None:
            previous_payload = latest_planning_payload(list(messages[:-1]))
            if previous_payload is None:
                return None
            try:
                payload = replan_from_payload(
                    previous_payload,
                    preference_updates={},
                    selected_mode=selected_mode,
                )
            except ValueError as exc:
                return {
                    "jump_to": "end",
                    "messages": [AIMessage(content=f"无法选择该方案：{exc}。")],
                }
            rendered = render_planning_payload(payload)
            if rendered is None:
                return None
            return {
                "jump_to": "end",
                "messages": [
                    AIMessage(
                        content=(
                            f"已选择{_MODE_NAMES[selected_mode]}方案。"
                            "接下来可以继续调整该方案的细节，或确认保存。\n\n"
                            f"{rendered}"
                        ),
                        additional_kwargs={
                            "travel_planning_payload": payload,
                            "travel_planning_update": {
                                "reused_previous_data": True,
                                "manual_mode_selection": True,
                            },
                        },
                    )
                ],
            }
        preference_updates = parse_preference_updates(content)
        if not preference_updates:
            return None

        previous_payload = latest_planning_payload(list(messages[:-1]))
        if previous_payload is None:
            return None
        context_value = previous_payload.get("context")
        if not isinstance(context_value, dict):
            return None
        try:
            context = JourneyContext.model_validate(context_value)
            snapshot_is_fresh = is_route_snapshot_fresh(
                context,
                reference_at=self._clock(),
            )
        except ValueError:
            return None
        transit_strategy_changed = "transit_strategy" in preference_updates
        if not snapshot_is_fresh or transit_strategy_changed:
            try:
                refresh_args = build_route_refresh_args(
                    context,
                    preference_updates=preference_updates,
                )
            except ValueError:
                return None
            return {
                "jump_to": "tools",
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "recommend_travel_plan",
                                "args": refresh_args,
                                "id": f"refresh-plan-{uuid4().hex}",
                                "type": "tool_call",
                            }
                        ],
                        additional_kwargs={
                            "travel_planning_update": {
                                "reused_previous_data": False,
                                "refresh_reason": (
                                    "transit_strategy_changed"
                                    if transit_strategy_changed
                                    else "route_snapshot_expired"
                                ),
                            },
                            "travel_refresh_fallback_payload": previous_payload,
                            "travel_refresh_preference_updates": preference_updates,
                            "travel_refresh_previous_mode": (
                                previous_payload.get("recommendation", {}).get(
                                    "recommended_mode"
                                )
                                if isinstance(
                                    previous_payload.get("recommendation"), dict
                                )
                                else None
                            ),
                        },
                    )
                ],
            }
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
