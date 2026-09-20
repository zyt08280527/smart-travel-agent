"""Translate LangGraph stream parts into the application's event protocol."""

import json
from uuid import UUID

from langchain.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from travel_agent.api.schemas import (
    AgentStreamEvent,
    ItineraryListResultCard,
    PlaceCandidateCard,
    PlaceResultCard,
    PlanningExcludedOptionCard,
    PlanningOptionCard,
    PlanningPreferenceCard,
    PlanningResultCard,
    PlanningTransitCandidateCard,
    PlanningTransitLegCard,
    PlanningVariantCard,
    RouteResultCard,
    TransitResultCard,
    WeatherResultCard,
)
from travel_agent.domain.itinerary import SavedItinerary


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
            geometry=payload.get("geometry", []),
            attribution=payload["attribution"],
        )
    except (KeyError, TypeError, ValidationError):
        return None


def _transit_candidate_cards(
    candidates: list[object],
    *,
    selected_index: int,
) -> list[PlanningTransitCandidateCard]:
    """Normalize provider alternatives into safe public-transport cards."""
    cards: list[PlanningTransitCandidateCard] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue
        legs = candidate.get("legs", [])
        line_names = (
            list(
                dict.fromkeys(
                    leg["line_name"]
                    for leg in legs
                    if isinstance(leg, dict)
                    and isinstance(leg.get("line_name"), str)
                    and leg["line_name"]
                )
            )
            if isinstance(legs, list)
            else []
        )
        leg_cards = (
            [
                PlanningTransitLegCard.model_validate(leg)
                for leg in legs
                if isinstance(leg, dict)
            ]
            if isinstance(legs, list)
            else []
        )
        cards.append(
            PlanningTransitCandidateCard(
                candidate_index=index,
                selected=index == selected_index,
                duration_s=candidate["duration_s"],
                cost_yuan=candidate.get("cost_yuan"),
                walking_distance_m=candidate["walking_distance_m"],
                transfer_count=candidate["transfer_count"],
                line_names=line_names,
                legs=leg_cards,
                geometry=candidate.get("geometry", []),
            )
        )
    return cards


def _transit_card_from_payload(
    payload: dict[str, object],
) -> TransitResultCard | None:
    """Build a safe structured view of every public-transport option."""
    if payload.get("ok") is False:
        return None
    if payload.get("mode") != "transit":
        return None

    options = payload.get("options")
    if not isinstance(options, list) or not options:
        return None
    selected_index = payload.get("selected_transit_candidate_index", 0)
    if not isinstance(selected_index, int) or not 0 <= selected_index < len(options):
        return None
    option = options[selected_index]
    if not isinstance(option, dict):
        return None

    try:
        candidate_cards = _transit_candidate_cards(
            options,
            selected_index=selected_index,
        )
        return TransitResultCard(
            type="transit",
            option_count=len(options),
            distance_m=option["distance_m"],
            duration_s=option["duration_s"],
            walking_distance_m=option["walking_distance_m"],
            cost_yuan=option.get("cost_yuan"),
            transfer_count=option["transfer_count"],
            line_names=candidate_cards[selected_index].line_names,
            options=candidate_cards,
            attribution=payload["attribution"],
        )
    except (KeyError, TypeError, ValidationError):
        return None


def _transit_card(message: ToolMessage) -> TransitResultCard | None:
    if message.name != "plan_transit_route":
        return None
    payload = _tool_json_payload(message)
    if payload is None:
        return None
    return _transit_card_from_payload(payload)


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
    route_refreshed: bool = False,
    used_stale_snapshot: bool = False,
    refresh_failed: bool = False,
    previous_recommended_mode: object = None,
) -> PlanningResultCard | None:
    """Build a recommendation card from a safe subset of planning data."""
    context = payload.get("context")
    recommendation = payload.get("recommendation")
    if not isinstance(context, dict) or not isinstance(recommendation, dict):
        return None
    preferences = context.get("preferences")
    arrival_deadline = context.get("arrival_deadline")
    ranked = recommendation.get("ranked_options")
    variants = payload.get("recommendation_variants", [])
    unavailable = recommendation.get("unavailable_options", [])
    transit_candidates = payload.get("transit_candidates", [])
    selected_transit_candidate_index = payload.get(
        "selected_transit_candidate_index"
    )
    if not isinstance(preferences, dict) or not isinstance(ranked, list):
        return None
    if not isinstance(variants, list):
        return None
    if not isinstance(unavailable, list):
        return None
    if not isinstance(transit_candidates, list):
        return None

    def ranked_cards_from(raw_ranked: list[object]) -> list[PlanningOptionCard]:
        cards: list[PlanningOptionCard] = []
        for scored in raw_ranked:
            if not isinstance(scored, dict):
                continue
            option = scored.get("option")
            scores = scored.get("scores")
            if not isinstance(option, dict) or not isinstance(scores, dict):
                continue
            cards.append(
                PlanningOptionCard(
                    mode=option["mode"],
                    total_score=scores["total"],
                    duration_s=option["duration_s"],
                    cost_yuan=option.get("cost_yuan"),
                    walking_distance_m=option.get("walking_distance_m"),
                    transfer_count=option.get("transfer_count"),
                    latest_departure_at=option.get("latest_departure_at"),
                    geometry=option.get("geometry", []),
                )
            )
        return cards

    excluded_cards: list[PlanningExcludedOptionCard] = []
    try:
        ranked_cards = ranked_cards_from(ranked)
        variant_cards: list[PlanningVariantCard] = []
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            variant_ranked = variant.get("ranked_options")
            if not isinstance(variant_ranked, list):
                continue
            variant_cards.append(
                PlanningVariantCard(
                    priority=variant["priority"],
                    recommended_mode=variant["recommended_mode"],
                    ranked_options=ranked_cards_from(variant_ranked),
                )
            )
        if not variant_cards:
            variant_cards.append(
                PlanningVariantCard(
                    priority=preferences["priority"],
                    recommended_mode=recommendation["recommended_mode"],
                    ranked_options=ranked_cards,
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
        transit_candidate_cards = _transit_candidate_cards(
            transit_candidates,
            selected_index=selected_transit_candidate_index,
        )
        return PlanningResultCard(
            type="planning",
            origin_name=context["origin_name"],
            destination_name=context["destination_name"],
            recommended_mode=recommendation["recommended_mode"],
            selected_mode=payload.get("selected_mode"),
            previous_recommended_mode=previous_recommended_mode,
            reused_previous_data=reused_previous_data,
            route_refreshed=route_refreshed,
            used_stale_snapshot=used_stale_snapshot,
            refresh_failed=refresh_failed,
            route_snapshot_at=context.get("route_snapshot_at"),
            arrival_by=(
                arrival_deadline.get("arrival_by")
                if isinstance(arrival_deadline, dict)
                else None
            ),
            arrival_buffer_minutes=(
                arrival_deadline.get("buffer_minutes")
                if isinstance(arrival_deadline, dict)
                else None
            ),
            preferences=PlanningPreferenceCard.model_validate(preferences),
            ranked_options=ranked_cards,
            recommendation_variants=variant_cards,
            unavailable_options=excluded_cards,
            transit_candidates=transit_candidate_cards,
            selected_transit_candidate_index=selected_transit_candidate_index,
        )
    except (KeyError, TypeError, ValidationError):
        return None


def _planning_tool_card(message: ToolMessage) -> PlanningResultCard | None:
    if message.name != "recommend_travel_plan":
        return None
    payload = _tool_json_payload(message)
    if payload is None or payload.get("ok") is False:
        return None
    return _planning_card_from_payload(
        payload,
        route_refreshed=message.tool_call_id.startswith("refresh-plan-"),
    )


def result_card_from_ai_message(
    message: AIMessage,
) -> PlanningResultCard | TransitResultCard | None:
    """Expose locally updated route state stored on an AI message."""
    transit_payload = message.additional_kwargs.get("transit_route_payload")
    if isinstance(transit_payload, dict):
        return _transit_card_from_payload(transit_payload)
    payload = message.additional_kwargs.get("travel_planning_payload")
    metadata = message.additional_kwargs.get("travel_planning_update", {})
    if not isinstance(payload, dict) or not isinstance(metadata, dict):
        return None
    return _planning_card_from_payload(
        payload,
        reused_previous_data=metadata.get("reused_previous_data") is True,
        route_refreshed=metadata.get("route_refreshed") is True,
        used_stale_snapshot=metadata.get("used_stale_snapshot") is True,
        refresh_failed=metadata.get("refresh_failed") is True,
        previous_recommended_mode=metadata.get("previous_recommended_mode"),
    )


def _itinerary_list_card(message: ToolMessage) -> ItineraryListResultCard | None:
    if message.name != "list_itineraries":
        return None
    payload = _tool_json_payload(message)
    if payload is None or payload.get("ok") is False:
        return None
    itineraries = payload.get("itineraries")
    if not isinstance(itineraries, list):
        return None
    try:
        records = [SavedItinerary.model_validate(item) for item in itineraries]
        return ItineraryListResultCard(
            type="itinerary_list",
            count=len(records),
            itineraries=records,
        )
    except (TypeError, ValidationError):
        return None


def result_card_from_tool_message(
    message: ToolMessage,
) -> (
    WeatherResultCard
    | RouteResultCard
    | TransitResultCard
    | PlaceResultCard
    | ItineraryListResultCard
    | None
):
    """Build one public result card from a supported tool message."""
    return (
        _weather_card(message)
        or _route_card(message)
        or _transit_card(message)
        or _place_card(message)
        or _planning_tool_card(message)
        or _itinerary_list_card(message)
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
