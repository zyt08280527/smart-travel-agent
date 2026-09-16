"""Ground itinerary writes in the route candidate selected by the user."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, ToolMessage

from travel_agent.domain.decision import TravelComparisonResult
from travel_agent.domain.transit import TransitOption, TransitPlan


def _latest_route_payload(
    messages: list[object],
) -> tuple[str, dict[str, object], bool] | None:
    """Return the newest route snapshot and whether transit was manually selected."""
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            planning = message.additional_kwargs.get("travel_planning_payload")
            if isinstance(planning, dict):
                update = message.additional_kwargs.get("travel_planning_update")
                manual_selection = (
                    isinstance(update, dict)
                    and update.get("manual_transit_selection") is True
                )
                return "planning", planning, manual_selection
            transit = message.additional_kwargs.get("transit_route_payload")
            if isinstance(transit, dict):
                return "transit", transit, True
        if not isinstance(message, ToolMessage):
            continue
        content = message.content
        if not isinstance(content, str):
            continue
        try:
            import json

            payload = json.loads(content)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict) or payload.get("ok") is False:
            continue
        if message.name == "recommend_travel_plan":
            return "planning", payload, False
        if message.name == "plan_transit_route":
            return "transit", payload, False
    return None


def _transit_notes(option: TransitOption) -> str:
    line_names = list(
        dict.fromkeys(leg.line_name for leg in option.legs if leg.line_name)
    )
    details = [
        f"接驳步行约 {round(option.walking_distance_m)} 米",
        f"换乘 {option.transfer_count} 次",
    ]
    if line_names:
        details.insert(0, "线路：" + " → ".join(line_names))
    if option.cost_yuan is not None:
        details.append(f"公共交通费用约 {option.cost_yuan:g} 元")
    return "；".join(details)


def _ground_from_transit(
    payload: dict[str, object], args: dict[str, Any]
) -> dict[str, Any]:
    plan = TransitPlan.model_validate(payload)
    raw_index = payload.get("selected_transit_candidate_index", 0)
    index = raw_index if isinstance(raw_index, int) else 0
    if not 0 <= index < len(plan.options):
        index = 0
    option = plan.options[index]
    grounded = dict(args)
    grounded.update(
        {
            "travel_mode": "transit",
            "distance_m": option.distance_m,
            "duration_s": option.duration_s,
            "duration_basis": "static_without_live_traffic",
            "notes": _transit_notes(option),
        }
    )
    return grounded


def _ground_from_planning(
    payload: dict[str, object],
    args: dict[str, Any],
    *,
    manual_transit_selection: bool,
) -> dict[str, Any]:
    result = TravelComparisonResult.model_validate(payload)
    grounded = dict(args)
    grounded["origin"] = result.context.origin_name
    grounded["destination"] = result.context.destination_name

    if manual_transit_selection and result.selected_transit_candidate_index is not None:
        option = result.transit_candidates[result.selected_transit_candidate_index]
        grounded.update(
            {
                "travel_mode": "transit",
                "distance_m": option.distance_m,
                "duration_s": option.duration_s,
                "duration_basis": "static_without_live_traffic",
                "notes": _transit_notes(option),
            }
        )
        return grounded

    option = result.recommendation.ranked_options[0].option
    grounded.update(
        {
            "travel_mode": option.mode,
            "distance_m": option.distance_m,
            "duration_s": option.duration_s,
            "duration_basis": option.duration_basis,
        }
    )
    return grounded


class ItinerarySaveGroundingMiddleware(AgentMiddleware):
    """Correct save arguments from trusted route state before HITL approval."""

    def after_model(
        self,
        state: AgentState[Any],
        runtime: object,
    ) -> dict[str, Any] | None:
        del runtime
        messages = state.get("messages", [])
        if not isinstance(messages, list) or not messages:
            return None
        last_ai = next(
            (message for message in reversed(messages) if isinstance(message, AIMessage)),
            None,
        )
        if last_ai is None or not last_ai.tool_calls:
            return None
        if not any(call.get("name") == "save_itinerary" for call in last_ai.tool_calls):
            return None

        route = _latest_route_payload(
            [message for message in messages if message is not last_ai]
        )
        if route is None:
            return None
        route_type, payload, manual_transit_selection = route

        revised_calls = []
        changed = False
        for call in last_ai.tool_calls:
            if call.get("name") != "save_itinerary":
                revised_calls.append(call)
                continue
            args = call.get("args")
            if not isinstance(args, dict):
                revised_calls.append(call)
                continue
            try:
                grounded_args = (
                    _ground_from_transit(payload, args)
                    if route_type == "transit"
                    else _ground_from_planning(
                        payload,
                        args,
                        manual_transit_selection=manual_transit_selection,
                    )
                )
            except ValueError:
                revised_calls.append(call)
                continue
            revised_calls.append({**call, "args": grounded_args})
            changed = changed or grounded_args != args

        if not changed:
            return None
        return {"messages": [last_ai.model_copy(update={"tool_calls": revised_calls})]}
