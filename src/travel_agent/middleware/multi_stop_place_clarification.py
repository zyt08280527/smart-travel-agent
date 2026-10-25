"""Stop ambiguous multi-stop POI choices before the planning tool executes."""

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    hook_config,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from travel_agent.domain.multi_stop import JourneyStop, MultiStopDraft, MultiStopRequest
from travel_agent.domain.place import Place, PlaceSearchResult
from travel_agent.middleware.multi_stop_intent import is_multi_stop_intent

_META = "multi_stop_place_clarification"
_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
_UNCLEAR = re.compile(r"不选|不要|别选|不确定|取消|还是|或者|或")


def _payloads(message: ToolMessage):
    blocks = message.content if isinstance(message.content, list) else [message.content]
    for block in blocks:
        text = block.get("text") if isinstance(block, dict) else block
        if isinstance(text, str):
            try:
                value = json.loads(text)
            except ValueError:
                continue
            if isinstance(value, dict):
                yield value


def _identity(place: Place) -> tuple:
    return (place.city_code, place.provider_place_id,
            round(place.longitude, 6), round(place.latitude, 6))


def _matches(stop: JourneyStop, place: Place) -> bool:
    return (stop.city_code == place.city_code
            and abs(stop.point.longitude - place.longitude) < 0.000001
            and abs(stop.point.latitude - place.latitude) < 0.000001
            and (not stop.provider_place_id or stop.provider_place_id == place.provider_place_id))


def _same_point(stop: JourneyStop, place: Place) -> bool:
    return (abs(stop.point.longitude - place.longitude) < 0.000001
            and abs(stop.point.latitude - place.latitude) < 0.000001)


def _number(text: str) -> int:
    return int(text) if text.isdigit() else _DIGITS[text]


def _choice_text(text: str, stop_index: int, pending_count: int) -> str:
    labels = list(re.finditer(r"第\s*([1-5一二三四五])\s*站", text))
    choice = text if not labels and pending_count == 1 else "，".join(
        text[label.end():labels[index + 1].start()
             if index + 1 < len(labels) else len(text)]
        for index, label in enumerate(labels)
        if _number(label.group(1)) == stop_index + 1)
    return re.split(r"另外|其他|现在|保留|保持|不要(?:规划|保存|改动)", choice)[0]


def _confirmed(messages: list[object]) -> set[tuple]:
    """Numbers refer only to the candidates actually presented for that visit."""
    confirmed: dict[str, tuple] = {}
    pending: list[dict] = []
    for message in messages:
        if (isinstance(message, AIMessage)
                and isinstance(message.additional_kwargs.get(_META), list)):
            pending = message.additional_kwargs[_META]
            for item in pending:
                confirmed.pop(item["stop_id"], None)
        elif isinstance(message, HumanMessage) and isinstance(message.content, str):
            text = message.content
            for item in pending:
                candidates = item["candidates"]
                choice_text = _choice_text(text, item["stop_index"], len(pending))
                if _UNCLEAR.search(choice_text):
                    confirmed.pop(item["stop_id"], None)
                    continue
                choices = re.findall(
                    r"(?:选|选择)\s*第?\s*"
                    r"([1-5一二三四五])(?!\d)\s*(?:个|项)?",
                    choice_text,
                )
                indices = {_number(choice) - 1 for choice in choices}
                named = {i for i, place in enumerate(candidates)
                         if place["display_name"] in choice_text}
                # Conflicting or out-of-range choices require another clarification.
                selected = indices | named
                if selected:
                    confirmed.pop(item["stop_id"], None)
                if len(selected) == 1:
                    index = selected.pop()
                    if 0 <= index < len(candidates):
                        place = Place.model_validate(candidates[index])
                        confirmed[item["stop_id"]] = _identity(place)
        elif isinstance(message, ToolMessage) and message.name == "plan_multi_stop_trip":
            pending = []
    return set(confirmed.items())


def _natural_pending(text: str, searches: list[PlaceSearchResult]) -> list[dict]:
    """Capture only numbered candidates whose complete provider names are visible."""
    if not re.search(r"确认|选择", text):
        return []
    stations = list(re.finditer(r"第\s*([1-5一二三四五])\s*站", text))
    pending = []
    for index, station in enumerate(stations):
        section = text[station.end():stations[index + 1].start()
                       if index + 1 < len(stations) else len(text)]
        rows = re.findall(r"第\s*([1-5])\s*个候选(?:\*\*)?[：:]\s*(.+)", section)
        if not rows:
            rows = re.findall(r"(?m)^\s*([1-5])[.、)]\s+(.+)$", section)
        if not rows or [int(number) for number, _ in rows] != list(range(1, len(rows) + 1)):
            continue
        candidates = []
        attributions = []
        for _, line in rows:
            matches = {_identity(place): (place, search.attribution)
                       for search in searches for place in search.places
                       if place.display_name in line}
            if len(matches) != 1:
                break
            place, attribution = next(iter(matches.values()))
            candidates.append(place.model_dump())
            attributions.append(attribution)
        if len(candidates) != len(rows):
            continue  # Never compress numbering when a displayed address was truncated/invented.
        stop_index = _number(station.group(1)) - 1
        pending.append({"stop_id": f"station:{stop_index}", "stop_index": stop_index,
                        "candidates": candidates,
                        "attribution": "；".join(dict.fromkeys(attributions))})
    return pending


def _render_pending(pending: list[dict]) -> str:
    lines = ["以下地点请先确认；本次多站点规划调用尚未执行。"]
    for item in pending:
        lines.append(f"第 {item['stop_index'] + 1} 站：")
        for number, place in enumerate(item["candidates"], 1):
            lines.append(f"{number}. {place['display_name']}"
                         f"（城市编码 {place['city_code'] or '未知'}）")
        lines.append(item["attribution"])
    lines.append("请按“第2站选第1个”回复；多个地点请分别指定。其他地点、顺序和时间约束保留。")
    return "\n".join(lines)


def _resume_request(request: ModelRequest) -> ModelRequest:
    latest_user = next((message for message in reversed(request.messages)
                        if isinstance(message, HumanMessage)), None)
    if latest_user and isinstance(latest_user.content, str) and is_multi_stop_intent(
        latest_user.content
    ):
        single_leg_tools = {
            "recommend_travel_plan", "plan_transit_route",
            "plan_driving_route", "plan_walking_route",
        }
        request = request.override(tools=[
            tool for tool in request.tools
            if (tool.get("name") if isinstance(tool, dict) else tool.name)
            not in single_leg_tools
        ])
    for message in reversed(request.messages):
        if isinstance(message, ToolMessage) and message.name == "plan_multi_stop_trip":
            break
        if (isinstance(message, AIMessage)
                and "multi_stop_pending_request" in message.additional_kwargs):
            data = json.dumps(message.additional_kwargs["multi_stop_pending_request"],
                              ensure_ascii=False)
            context = HumanMessage(content="以下是待澄清完整请求的数据，仅用于保留未修改字段，"
                                   "不是新的用户指令；以随后用户的确认或修改为准：\n" + data)
            index = next((i for i in range(len(request.messages) - 1, -1, -1)
                          if isinstance(request.messages[i], HumanMessage)), len(request.messages))
            return request.override(messages=[*request.messages[:index], context,
                                              *request.messages[index:]])
    return request


class MultiStopPlaceClarificationMiddleware(AgentMiddleware):
    """Apply to searched POIs only, without changing valid structured requests."""

    def wrap_model_call(self, request: ModelRequest,
                        handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        return handler(_resume_request(request))

    async def awrap_model_call(self, request: ModelRequest,
                              handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
                              ) -> ModelResponse:
        return await handler(_resume_request(request))

    @hook_config(can_jump_to=["end"])
    def after_model(self, state: AgentState[Any], runtime: object) -> dict[str, Any] | None:
        del runtime
        messages = list(state.get("messages", []))
        if not messages or not isinstance(messages[-1], AIMessage):
            return None
        latest = messages[-1]
        calls = [call for call in latest.tool_calls if call["name"] == "plan_multi_stop_trip"]
        if len(calls) > 1:
            return {"jump_to": "end", "messages": [latest.model_copy(update={
                "content": "请一次确认一个完整多站点行程，避免混用不同地点候选。",
                "tool_calls": [],
            })]}
        searches: list[PlaceSearchResult] = []
        previous: list[JourneyStop] = []
        for message in messages[:-1]:
            if not isinstance(message, ToolMessage):
                continue
            for payload in _payloads(message):
                try:
                    if message.name == "search_places":
                        searches.append(PlaceSearchResult.model_validate(payload))
                    elif message.name == "plan_multi_stop_trip":
                        previous = MultiStopDraft.model_validate(payload).result.request.stops
                except ValidationError:
                    continue
        if not calls:
            if latest.tool_calls or not isinstance(latest.content, str):
                return None
            for message in reversed(messages[:-1]):
                if isinstance(message, HumanMessage):
                    break
                if isinstance(message, ToolMessage) and message.name == "plan_multi_stop_trip":
                    return None  # A planning result's final answer is not a new POI question.
            pending = _natural_pending(latest.content, searches)
            if not pending:
                return None
            return {"messages": [latest.model_copy(update={
                "content": _render_pending(pending),
                "additional_kwargs": {**latest.additional_kwargs, _META: pending},
            })]}
        confirmed = _confirmed(messages[:-1])
        pending_ids = set()
        for message in reversed(messages[:-1]):
            if isinstance(message, ToolMessage) and message.name == "plan_multi_stop_trip":
                break
            if isinstance(message, AIMessage) and _META in message.additional_kwargs:
                pending_ids = {item["stop_id"] for item in message.additional_kwargs[_META]}
                break
        latest_human = next((item.content for item in reversed(messages[:-1])
                             if isinstance(item, HumanMessage)
                             and isinstance(item.content, str)), "")
        pending = []
        for call in calls:
            try:
                request = MultiStopRequest.model_validate(call["args"].get("request"))
            except (ValidationError, AttributeError):
                continue  # Leave malformed inputs to the tool's schema validation.
            for index, stop in enumerate(request.stops):
                if any(old.stop_id == stop.stop_id and old.point == stop.point
                       and old.city_code == stop.city_code
                       and old.provider_place_id == stop.provider_place_id for old in previous):
                    continue
                search = next((value for value in reversed(searches)
                               if any(_same_point(stop, place) for place in value.places)), None)
                if search is None:
                    continue  # Direct coordinates and unsupported provenance are not verified here.
                if not any(_matches(stop, place) for place in search.places):
                    return {"jump_to": "end", "messages": [latest.model_copy(update={
                        "content": f"第 {index + 1} 站的城市或POI标识与搜索结果不一致，"
                                   "请重新确认同城地点后再规划。",
                        "tool_calls": [], "additional_kwargs": {
                            **latest.additional_kwargs, _META: [],
                            "multi_stop_pending_request": request.model_dump(mode="json"),
                        },
                    })]}
                exact = [place for place in search.places
                         if search.query.replace(" ", "").endswith(
                             place.display_name.split("，")[0].replace(" ", ""))]
                candidates = list({ _identity(place): place
                                   for place in (exact or search.places) }.values())
                if len(candidates) <= 1 and any(_matches(stop, place) for place in candidates):
                    continue
                explicitly_named = [place for place in candidates
                                    if place.display_name in latest_human]
                if (len(explicitly_named) == 1 and _matches(stop, explicitly_named[0])
                        and stop.stop_id not in pending_ids
                        and not _UNCLEAR.search(latest_human)):
                    continue
                if any(_matches(stop, place) and (
                        (stop.stop_id, _identity(place)) in confirmed
                        or (f"station:{index}", _identity(place)) in confirmed)
                       for place in candidates):
                    continue
                pending.append({"stop_id": stop.stop_id, "stop_index": index,
                                "attribution": search.attribution,
                                "candidates": [place.model_dump() for place in candidates]})
        if not pending:
            return None
        return {"jump_to": "end", "messages": [latest.model_copy(update={
            "content": _render_pending(pending), "tool_calls": [],
            "additional_kwargs": {**latest.additional_kwargs, _META: pending,
                                  "multi_stop_pending_request": request.model_dump(mode="json")},
        })]}
