import json

from langchain.messages import AIMessage, HumanMessage, ToolMessage

PRECISE_LOCATION_TERMS = ("校区", "景点", "精确坐标", "具体地点")
WEATHER_CAPABILITY_TERMS = ("查询", "获取", "提供", "查看")
CAPABILITY_NEGATIONS = ("不能", "无法", "不支持", "仅支持城市", "只能查询城市")
WEATHER_CAPABILITY_NOTICE = (
    "能力说明：当前天气工具仅支持城市级查询，"
    "不能查询具体校区、景点或精确坐标的天气。"
)
ITINERARY_EXPORT_NEGATIONS = ("不能", "无法", "不支持", "暂不支持")
ITINERARY_CAPABILITY_NOTICE = (
    "能力说明：当前支持本地保存行程，暂不支持导出行程。"
)
UV_CLAIM_TERMS = ("紫外线较强", "紫外线很强", "紫外线强烈", "紫外线较弱")
UV_DATA_NEGATIONS = ("未提供", "没有提供", "无法判断", "未知", "未查询")
UV_DATA_NOTICE = (
    "数据边界：天气工具未提供紫外线数据；防晒建议仅属于一般性建议。"
)
LIVE_TRAFFIC_CLAIM_TERMS = (
    "无拥堵提示",
    "当前无拥堵",
    "路况畅通",
    "实时路况正常",
    "拥堵较少",
)
LIVE_TRAFFIC_NOTICE = (
    "数据边界：路线工具不提供实时拥堵信息，请在出发前通过实时导航确认。"
)
TRANSIT_COST_BOUNDARY_TERMS = (
    "无法判断",
    "不能判断",
    "无法确认",
    "不能确认",
    "未知",
    "不确定",
)
TRANSIT_COST_NOTICE = (
    "数据边界：公交路线工具未提供费用信息，无法判断是否免费。"
)
TRANSIT_LIVE_TERMS = (
    "实时班次",
    "当前班次",
    "实时运营状态",
    "运营状态",
    "实时动态",
    "车辆到站",
)
TRANSIT_LIVE_NEGATIONS = ("不提供", "未提供", "无法", "不能")
TRANSIT_LIVE_NOTICE = (
    "数据边界：公交路线工具不提供实时班次、车辆到站或运营状态，"
    "请在出发前向运营方核实。"
)
ATTRIBUTION_TOOL_NAMES = {
    "search_places",
    "resolve_route_endpoints",
    "plan_driving_route",
    "plan_walking_route",
    "plan_transit_route",
}


def _has_unsupported_weather_claim(text: str) -> bool:
    """Return whether one line claims unsupported precise-location weather."""
    return (
        "天气" in text
        and any(term in text for term in PRECISE_LOCATION_TERMS)
        and any(term in text for term in WEATHER_CAPABILITY_TERMS)
        and not any(term in text for term in CAPABILITY_NEGATIONS)
    )


def _apply_capability_policy(
    answer: str,
    *,
    transit_cost_unknown: bool = False,
    transit_route_used: bool = False,
) -> str:
    """Remove unsupported capability lines and append a deterministic notice."""
    kept_lines: list[str] = []
    removed_weather_claim = False
    removed_export_claim = False
    removed_uv_claim = False
    removed_traffic_claim = False
    removed_transit_cost_claim = False
    removed_transit_live_claim = False
    for line in answer.splitlines():
        if _has_unsupported_weather_claim(line):
            removed_weather_claim = True
            continue
        if (
            "导出行程" in line
            and not any(term in line for term in ITINERARY_EXPORT_NEGATIONS)
        ):
            removed_export_claim = True
            continue
        if (
            any(term in line for term in UV_CLAIM_TERMS)
            and not any(term in line for term in UV_DATA_NEGATIONS)
        ):
            removed_uv_claim = True
            continue
        if any(term in line for term in LIVE_TRAFFIC_CLAIM_TERMS):
            removed_traffic_claim = True
            continue
        if (
            transit_cost_unknown
            and "免费" in line
            and not any(
                term in line for term in TRANSIT_COST_BOUNDARY_TERMS
            )
        ):
            removed_transit_cost_claim = True
            continue
        if (
            transit_route_used
            and any(term in line for term in TRANSIT_LIVE_TERMS)
            and not any(term in line for term in TRANSIT_LIVE_NEGATIONS)
        ):
            removed_transit_live_claim = True
            continue
        kept_lines.append(line)

    sanitized_answer = "\n".join(kept_lines).strip()
    notices: list[str] = []
    if removed_weather_claim:
        notices.append(WEATHER_CAPABILITY_NOTICE)
    if removed_export_claim:
        notices.append(ITINERARY_CAPABILITY_NOTICE)
    if removed_uv_claim:
        notices.append(UV_DATA_NOTICE)
    if removed_traffic_claim:
        notices.append(LIVE_TRAFFIC_NOTICE)
    if removed_transit_cost_claim:
        notices.append(TRANSIT_COST_NOTICE)
    if removed_transit_live_claim:
        notices.append(TRANSIT_LIVE_NOTICE)
    if notices:
        notice_text = "\n\n".join(notices)
        sanitized_answer = f"{sanitized_answer}\n\n{notice_text}"
    return sanitized_answer


def _tool_json_payloads(message: ToolMessage) -> list[dict[str, object]]:
    """Parse JSON objects from one MCP ToolMessage's text content blocks."""
    content_blocks = (
        message.content if isinstance(message.content, list) else [message.content]
    )
    payloads: list[dict[str, object]] = []

    for block in content_blocks:
        if isinstance(block, dict):
            text = block.get("text")
        elif isinstance(block, str):
            text = block
        else:
            text = getattr(block, "text", None)
        if not isinstance(text, str):
            continue

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)

    return payloads


def _current_turn_messages(messages: list[object]) -> list[object]:
    """Return messages produced since the latest user message."""
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return messages[index:]
    return messages


def _uses_tool(messages: list[object], tool_name: str) -> bool:
    """Return whether the current turn contains one named tool result."""
    return any(
        isinstance(message, ToolMessage) and message.name == tool_name
        for message in messages
    )


def _transit_cost_is_unknown(messages: list[object]) -> bool:
    """Return whether a transit result explicitly omits every option's cost."""
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name != "plan_transit_route":
            continue
        for payload in _tool_json_payloads(message):
            options = payload.get("options")
            if not isinstance(options, list) or not options:
                continue
            costs = [
                option.get("cost_yuan")
                for option in options
                if isinstance(option, dict)
            ]
            if costs and all(cost is None for cost in costs):
                return True
    return False


def render_user_response(messages: list[object]) -> str:
    """Build user-visible text and deterministically preserve tool attribution."""
    final_answers = [
        str(message.content).strip()
        for message in messages
        if isinstance(message, AIMessage)
        and not message.tool_calls
        and str(message.content).strip()
    ]
    if not final_answers:
        raise ValueError("Agent 未生成最终回答")

    current_turn_messages = _current_turn_messages(messages)
    answer = _apply_capability_policy(
        final_answers[-1],
        transit_cost_unknown=_transit_cost_is_unknown(current_turn_messages),
        transit_route_used=_uses_tool(
            current_turn_messages,
            "plan_transit_route",
        ),
    )
    attributions: list[str] = []
    for message in current_turn_messages:
        if (
            not isinstance(message, ToolMessage)
            or message.name not in ATTRIBUTION_TOOL_NAMES
        ):
            continue
        for payload in _tool_json_payloads(message):
            attribution = payload.get("attribution")
            if (
                isinstance(attribution, str)
                and attribution
                and attribution not in attributions
            ):
                attributions.append(attribution)

    missing_attributions = [
        attribution for attribution in attributions if attribution not in answer
    ]
    if missing_attributions:
        answer = f"{answer}\n\n数据来源：{' ；'.join(missing_attributions)}"

    return answer
