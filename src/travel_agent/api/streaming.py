"""Translate LangGraph stream parts into the application's event protocol."""

import json
from uuid import UUID

from langchain.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from travel_agent.api.schemas import (
    AgentStreamEvent,
    PlaceCandidateCard,
    PlaceResultCard,
    PlanningExcludedOptionCard,
    PlanningOptionCard,
    PlanningPreferenceCard,
    PlanningResultCard,
    RouteResultCard,
    TransitResultCard,
    WeatherResultCard,
)


def _text_from_content(content: object) -> str:
    """Extract text from string or content-block message formats."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "".join(parts)


def _tool_json_payload(message: ToolMessage) -> dict[str, object] | None:
    """Parse one JSON object from an MCP tool message's text content."""
    text = _text_from_content(message.content)
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _weather_card(message: ToolMessage) -> WeatherResultCard | None:
    """Build a safe weather card without exposing the complete tool payload."""
    if message.name != "query_current_weather":
        return None
    payload = _tool_json_payload(message)
    if payload is None or payload.get("ok") is False:
        return None
    location = payload.get("location")
    if not isinstance(location, dict):
        return None

    try:
        return WeatherResultCard(
            type="weather",
            city=location["name"],
            country=location.get("country"),
            admin1=location.get("admin1"),
            temperature_c=payload["temperature_c"],
            apparent_temperature_c=payload["apparent_temperature_c"],
            precipitation_mm=payload["precipitation_mm"],
            wind_speed_kmh=payload["wind_speed_kmh"],
            condition=payload["condition"],
            observed_at=payload["observed_at"],
        )
    except (KeyError, TypeError, ValidationError):
        return None


def _route_card(message: ToolMessage) -> RouteResultCard | None:
    """Build a safe driving or walking summary from a route tool result."""
    expected_modes = {
        "plan_driving_route": "driving",
        "plan_walking_route": "walking",
    }
    expected_mode = expected_modes.get(message.name or "")
    if expected_mode is None:
        return None

    payload = _tool_json_payload(message)
    if payload is None or payload.get("ok") is False:
        return None
    if payload.get("mode") != expected_mode:
        return None

    traffic_status_counts: dict[str, int] = {}
    traffic_segments = payload.get("traffic_segments", [])
    if isinstance(traffic_segments, list):
        for segment in traffic_segments:
            if not isinstance(segment, dict):
                continue
            status = segment.get("status")
            if isinstance(status, str) and status:
                traffic_status_counts[status] = (
                    traffic_status_counts.get(status, 0) + 1
                )

    try:
        return RouteResultCard(
            type="route",
            mode=expected_mode,
            distance_m=payload["distance_m"],
            duration_s=payload["duration_s"],
            duration_basis=payload.get(
                "duration_basis",
                "static_without_live_traffic",
            ),
            tolls_yuan=payload.get("tolls_yuan"),
            taxi_cost_yuan=payload.get("taxi_cost_yuan"),
            traffic_lights=payload.get("traffic_lights"),
            restriction=payload.get("restriction"),
            traffic_status_counts=traffic_status_counts,
            step_count=payload["step_count"],
            attribution=payload["attribution"],
        )
    except (KeyError, TypeError, ValidationError):
        return None


def _transit_card(message: ToolMessage) -> TransitResultCard | None:
    """Build a safe summary of the first public-transport option."""
    if message.name != "plan_transit_route":
        return None
    payload = _tool_json_payload(message)
    if payload is None or payload.get("ok") is False:
        return None
    if payload.get("mode") != "transit":
        return None

    options = payload.get("options")
    if not isinstance(options, list) or not options:
        return None
    option = options[0]
    if not isinstance(option, dict):
        return None

    line_names: list[str] = []
    legs = option.get("legs", [])
    if isinstance(legs, list):
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            line_name = leg.get("line_name")
            if (
                isinstance(line_name, str)
                and line_name
                and line_name not in line_names
            ):
                line_names.append(line_name)

    try:
        return TransitResultCard(
            type="transit",
            option_count=len(options),
            distance_m=option["distance_m"],
            duration_s=option["duration_s"],
            walking_distance_m=option["walking_distance_m"],
            cost_yuan=option.get("cost_yuan"),
            transfer_count=option["transfer_count"],
            line_names=line_names,
            attribution=payload["attribution"],
        )
    except (KeyError, TypeError, ValidationError):
        return None


def _place_card(message: ToolMessage) -> PlaceResultCard | None:
    """Build a safe candidate list only for explicit place searches."""
    if message.name != "search_places":
        return None
    payload = _tool_json_payload(message)
    if payload is None or payload.get("ok") is False:
        return None
    places = payload.get("places")
    if not isinstance(places, list):
        return None

    try:
        candidates = [
            PlaceCandidateCard(
                display_name=place["display_name"],
                latitude=place["latitude"],
                longitude=place["longitude"],
                category=place.get("category"),
                place_type=place.get("place_type"),
            )
            for place in places
            if isinstance(place, dict)
        ]
        return PlaceResultCard(
            type="place",
            query=payload["query"],
            places=candidates,
            attribution=payload["attribution"],
        )
    except (KeyError, TypeError, ValidationError):
        return None


def _planning_card_from_payload(
    payload: dict[str, object],
    *,
    reused_previous_data: bool = False,
    previous_recommended_mode: object = None,
) -> PlanningResultCard | None:
    """Build a recommendation card from a safe subset of planning data."""
    context = payload.get("context")
    recommendation = payload.get("recommendation")
    if not isinstance(context, dict) or not isinstance(recommendation, dict):
        return None
    preferences = context.get("preferences")
    ranked = recommendation.get("ranked_options")
    unavailable = recommendation.get("unavailable_options", [])
    if not isinstance(preferences, dict) or not isinstance(ranked, list):
        return None
    if not isinstance(unavailable, list):
        return None

    ranked_cards: list[PlanningOptionCard] = []
    excluded_cards: list[PlanningExcludedOptionCard] = []
    try:
        for scored in ranked:
            if not isinstance(scored, dict):
                continue
            option = scored.get("option")
            scores = scored.get("scores")
            if not isinstance(option, dict) or not isinstance(scores, dict):
                continue
            ranked_cards.append(
                PlanningOptionCard(
                    mode=option["mode"],
                    total_score=scores["total"],
                    duration_s=option["duration_s"],
                    walking_distance_m=option.get("walking_distance_m"),
                    transfer_count=option.get("transfer_count"),
                )
            )
        for option in unavailable:
            if not isinstance(option, dict):
                continue
            reason = option.get("failure_reason")
            if not isinstance(reason, str) or not reason:
                continue
            excluded_cards.append(
                PlanningExcludedOptionCard(
                    mode=option["mode"],
                    reason=reason,
                )
            )
        return PlanningResultCard(
            type="planning",
            origin_name=context["origin_name"],
            destination_name=context["destination_name"],
            recommended_mode=recommendation["recommended_mode"],
            previous_recommended_mode=previous_recommended_mode,
            reused_previous_data=reused_previous_data,
            preferences=PlanningPreferenceCard.model_validate(preferences),
            ranked_options=ranked_cards,
            unavailable_options=excluded_cards,
        )
    except (KeyError, TypeError, ValidationError):
        return None


def _planning_tool_card(message: ToolMessage) -> PlanningResultCard | None:
    if message.name != "recommend_travel_plan":
        return None
    payload = _tool_json_payload(message)
    if payload is None or payload.get("ok") is False:
        return None
    return _planning_card_from_payload(payload)


def result_card_from_ai_message(message: AIMessage) -> PlanningResultCard | None:
    """Expose a local re-rank stored on a middleware-generated AI message."""
    payload = message.additional_kwargs.get("travel_planning_payload")
    metadata = message.additional_kwargs.get("travel_planning_update", {})
    if not isinstance(payload, dict) or not isinstance(metadata, dict):
        return None
    return _planning_card_from_payload(
        payload,
        reused_previous_data=metadata.get("reused_previous_data") is True,
        previous_recommended_mode=metadata.get("previous_recommended_mode"),
    )


def result_card_from_tool_message(
    message: ToolMessage,
) -> (
    WeatherResultCard
    | RouteResultCard
    | TransitResultCard
    | PlaceResultCard
    | None
):
    """Build one public result card from a supported tool message."""
    return (
        _weather_card(message)
        or _route_card(message)
        or _transit_card(message)
        or _place_card(message)
        or _planning_tool_card(message)
    )


def _message_events(
    data: object,
    thread_id: UUID,
) -> list[AgentStreamEvent]:
    """Convert model token chunks while ignoring tool-result chunks."""
    if not isinstance(data, tuple) or len(data) != 2:
        return []

    message_chunk, metadata = data
    if isinstance(message_chunk, ToolMessage):
        return []
    if not isinstance(metadata, dict):
        return []
    if metadata.get("langgraph_node") != "model":
        return []

    delta = _text_from_content(getattr(message_chunk, "content", ""))
    if not delta:
        return []
    return [
        AgentStreamEvent(
            type="assistant_delta",
            thread_id=thread_id,
            delta=delta,
        )
    ]


def _update_events(
    data: object,
    thread_id: UUID,
) -> list[AgentStreamEvent]:
    """Convert completed model tool calls and completed tool executions."""
    if not isinstance(data, dict):
        return []

    events: list[AgentStreamEvent] = []
    for update in data.values():
        if not isinstance(update, dict):
            continue

        messages = update.get("messages", [])
        if not isinstance(messages, list):
            messages = [messages]

        for message in messages:
            if isinstance(message, AIMessage):
                for tool_call in message.tool_calls:
                    events.append(
                        AgentStreamEvent(
                            type="tool_requested",
                            thread_id=thread_id,
                            tool_name=tool_call["name"],
                            tool_args=tool_call["args"],
                            tool_call_id=tool_call.get("id"),
                        )
                    )
                result_card = result_card_from_ai_message(message)
                if result_card is not None:
                    events.append(
                        AgentStreamEvent(
                            type="result_card",
                            thread_id=thread_id,
                            card=result_card,
                        )
                    )
            elif isinstance(message, ToolMessage):
                events.append(
                    AgentStreamEvent(
                        type="tool_completed",
                        thread_id=thread_id,
                        tool_name=message.name,
                        tool_call_id=message.tool_call_id,
                    )
                )
                result_card = result_card_from_tool_message(message)
                if result_card is not None:
                    events.append(
                        AgentStreamEvent(
                            type="result_card",
                            thread_id=thread_id,
                            tool_name=message.name,
                            tool_call_id=message.tool_call_id,
                            card=result_card,
                        )
                    )
    return events


def normalize_stream_part(
    part: object,
    thread_id: UUID,
) -> list[AgentStreamEvent]:
    """Return zero or more public events for one LangGraph v2 stream part."""
    if not isinstance(part, dict):
        return []

    event_type = part.get("type")
    if event_type == "messages":
        return _message_events(part.get("data"), thread_id)
    if event_type == "updates":
        return _update_events(part.get("data"), thread_id)
    return []
