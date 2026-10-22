"""Read immutable, content-addressed route snapshots from one graph thread."""

import hashlib
import json
from copy import deepcopy

from langchain_core.messages import AIMessage, ToolMessage

from travel_agent.domain.decision import TravelComparisonResult
from travel_agent.domain.transit import TransitPlan


def journey_versions(messages: list[object]) -> list[dict]:
    """Return validated comprehensive/transit snapshots, newest first, without writes."""
    versions: dict[str, dict] = {}
    for message in messages:
        candidates = []
        if isinstance(message, AIMessage):
            for kind, key in (("planning", "travel_planning_payload"),
                              ("transit", "transit_route_payload")):
                if isinstance(message.additional_kwargs.get(key), dict):
                    candidates.append((kind, message.additional_kwargs[key]))
        elif isinstance(message, ToolMessage) and message.name in {
            "recommend_travel_plan", "plan_transit_route",
        }:
            blocks = message.content if isinstance(message.content, list) else [message.content]
            for block in blocks:
                value = block.get("text") if isinstance(block, dict) else block
                if not isinstance(value, str):
                    continue
                try:
                    candidates.append((
                        "planning" if message.name == "recommend_travel_plan" else "transit",
                        json.loads(value),
                    ))
                except ValueError:
                    continue
        for kind, payload in candidates:
            try:
                model = TravelComparisonResult if kind == "planning" else TransitPlan
                model.model_validate(payload)
            except (ValueError, TypeError):
                continue
            canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            version_id = hashlib.sha256((kind + canonical).encode()).hexdigest()
            versions[version_id] = {
                "version_id": version_id, "kind": kind, "payload": deepcopy(payload),
            }
    return list(reversed(versions.values()))
