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
    "recommend_travel_plan",
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
    driving_traffic_available: bool = False,
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
        if (
            not driving_traffic_available
            and any(term in line for term in LIVE_TRAFFIC_CLAIM_TERMS)
        ):
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


def _planning_options(messages: list[object]) -> list[dict[str, object]]:
    """Extract normalized options nested in composite planning results."""
    options: list[dict[str, object]] = []
    for message in messages:
        if (
            not isinstance(message, ToolMessage)
            or message.name != "recommend_travel_plan"
        ):
            continue
        for payload in _tool_json_payloads(message):
            recommendation = payload.get("recommendation")
            if not isinstance(recommendation, dict):
                continue
            ranked = recommendation.get("ranked_options")
            if not isinstance(ranked, list):
                continue
            for scored in ranked:
                if not isinstance(scored, dict):
                    continue
                option = scored.get("option")
                if isinstance(option, dict):
                    options.append(option)
    return options


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
    transit_options = [
        option
        for option in _planning_options(messages)
        if option.get("mode") == "transit"
    ]
    return bool(transit_options) and all(
        option.get("cost_yuan") is None for option in transit_options
    )


def _driving_traffic_is_available(messages: list[object]) -> bool:
    """Return whether the driving result contains provider traffic segments."""
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name != "plan_driving_route":
            continue
        for payload in _tool_json_payloads(message):
            segments = payload.get("traffic_segments")
            if (
                payload.get("duration_basis") == "traffic_aware_estimate"
                and isinstance(segments, list)
                and segments
            ):
                return True
    return any(
        option.get("mode") == "driving"
        and option.get("duration_basis") == "traffic_aware_estimate"
        and isinstance(option.get("traffic_status_counts"), dict)
        and bool(option["traffic_status_counts"])
        for option in _planning_options(messages)
    )


def _collect_attributions(value: object) -> list[str]:
    """Collect unique attribution values from nested provider payloads."""
    collected: list[str] = []
    if isinstance(value, dict):
        attribution = value.get("attribution")
        if isinstance(attribution, str) and attribution:
            collected.append(attribution)
        for nested in value.values():
            for item in _collect_attributions(nested):
                if item not in collected:
                    collected.append(item)
    elif isinstance(value, list):
        for nested in value:
            for item in _collect_attributions(nested):
                if item not in collected:
                    collected.append(item)
    return collected


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _latest_planning_payload(
    messages: list[object],
) -> dict[str, object] | None:
    for message in reversed(messages):
        if (
            not isinstance(message, ToolMessage)
            or message.name != "recommend_travel_plan"
        ):
            continue
        payloads = _tool_json_payloads(message)
        if payloads and payloads[-1].get("ok") is not False:
            return payloads[-1]
    return None


def _route_summary_line(scored: object) -> str | None:
    if not isinstance(scored, dict):
        return None
    option = scored.get("option")
    scores = scored.get("scores")
    if not isinstance(option, dict) or not isinstance(scores, dict):
        return None
    mode = option.get("mode")
    mode_names = {"driving": "驾车", "transit": "公共交通", "walking": "步行"}
    mode_name = mode_names.get(mode)
    distance = _number(option.get("distance_m"))
    duration = _number(option.get("duration_s"))
    total = _number(scores.get("total"))
    if mode_name is None or distance is None or duration is None or total is None:
        return None

    details = [
        f"{distance / 1000:.1f} 公里",
        f"约 {round(duration / 60)} 分钟",
        f"综合得分 {total:.1f}",
    ]
    if mode == "driving":
        tolls = _number(option.get("tolls_yuan"))
        taxi_cost = _number(option.get("taxi_cost_yuan"))
        if tolls is not None:
            details.append(f"道路通行费 {tolls:g} 元")
        if taxi_cost is not None:
            details.append(f"出租车估价 {taxi_cost:g} 元")
        traffic_counts = option.get("traffic_status_counts")
        if isinstance(traffic_counts, dict) and traffic_counts:
            traffic_text = "、".join(
                f"{status}{count}段"
                for status, count in traffic_counts.items()
                if isinstance(status, str) and isinstance(count, int)
            )
            if traffic_text:
                details.append(f"查询时路况分段：{traffic_text}")
    elif mode == "transit":
        walking = _number(option.get("walking_distance_m"))
        transfers = option.get("transfer_count")
        cost = _number(option.get("cost_yuan"))
        if walking is not None:
            details.append(f"接驳步行 {walking:.0f} 米")
        if isinstance(transfers, int):
            details.append(f"换乘 {transfers} 次")
        details.append(
            f"票价 {cost:g} 元" if cost is not None else "票价未提供"
        )
    return f"- {mode_name}：{'；'.join(details)}"


def _render_planning_payload(payload: dict[str, object]) -> str | None:
    context = payload.get("context")
    recommendation = payload.get("recommendation")
    if not isinstance(context, dict) or not isinstance(recommendation, dict):
        return None
    origin = context.get("origin_name")
    destination = context.get("destination_name")
    ranked = recommendation.get("ranked_options")
    recommended_mode = recommendation.get("recommended_mode")
    if (
        not isinstance(origin, str)
        or not isinstance(destination, str)
        or not isinstance(ranked, list)
        or not ranked
        or recommended_mode not in {"driving", "transit", "walking"}
    ):
        return None

    mode_names = {"driving": "驾车", "transit": "公共交通", "walking": "步行"}
    lines = [
        f"{origin} → {destination} 出行比较",
        "",
    ]
    weather = context.get("weather")
    if isinstance(weather, dict):
        temperature = _number(weather.get("temperature_c"))
        apparent = _number(weather.get("apparent_temperature_c"))
        precipitation = _number(weather.get("precipitation_mm"))
        wind = _number(weather.get("wind_speed_kmh"))
        condition = weather.get("condition")
        weather_parts: list[str] = []
        if isinstance(condition, str):
            weather_parts.append(condition)
        if temperature is not None:
            weather_parts.append(f"实际温度 {temperature:g}℃")
        if apparent is not None:
            weather_parts.append(f"体感温度 {apparent:g}℃")
        if precipitation is not None:
            weather_parts.append(f"降水 {precipitation:g} 毫米")
        if wind is not None:
            weather_parts.append(f"风速 {wind:g} km/h")
        if weather_parts:
            lines.extend([f"当前天气：{'，'.join(weather_parts)}。", ""])

    confidence = _number(recommendation.get("confidence"))
    recommendation_text = f"推荐方式：{mode_names[recommended_mode]}"
    if confidence is not None:
        recommendation_text += f"（推荐区分度 {confidence * 100:.1f}%）"
    lines.extend([recommendation_text, "", "方案对比："])
    lines.extend(
        line for scored in ranked if (line := _route_summary_line(scored))
    )

    limitations = recommendation.get("limitations")
    if isinstance(limitations, list):
        valid_limitations = [item for item in limitations if isinstance(item, str)]
        if valid_limitations:
            lines.extend(["", "数据限制："])
            mode_labels = {
                "driving 方案": "驾车方案",
                "transit 方案": "公共交通方案",
                "walking 方案": "步行方案",
            }
            lines.extend(
                "- "
                + next(
                    (
                        item.replace(raw, label, 1)
                        for raw, label in mode_labels.items()
                        if item.startswith(raw)
                    ),
                    item,
                )
                for item in valid_limitations
            )
    lines.extend(
        [
            "",
            "驾车时长与路况是查询时快照；步行和公共交通为静态预计，"
            "出发前请通过实时导航再次确认。",
        ]
    )
    return "\n".join(lines)


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
    planning_payload = _latest_planning_payload(current_turn_messages)
    deterministic_planning_answer = (
        _render_planning_payload(planning_payload)
        if planning_payload is not None
        else None
    )
    answer = deterministic_planning_answer or _apply_capability_policy(
        final_answers[-1],
        transit_cost_unknown=_transit_cost_is_unknown(current_turn_messages),
        transit_route_used=(
            _uses_tool(current_turn_messages, "plan_transit_route")
            or any(
                option.get("mode") == "transit"
                for option in _planning_options(current_turn_messages)
            )
        ),
        driving_traffic_available=_driving_traffic_is_available(
            current_turn_messages
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
            for attribution in _collect_attributions(payload):
                if attribution not in attributions:
                    attributions.append(attribution)

    missing_attributions = [
        attribution for attribution in attributions if attribution not in answer
    ]
    if missing_attributions:
        answer = f"{answer}\n\n数据来源：{' ；'.join(missing_attributions)}"

    return answer
