import json

from fastapi.testclient import TestClient
from langchain_core.messages import ToolMessage

from tests.api.test_app import FakeRuntime
from tests.services.test_attraction import museum_place
from travel_agent.api.app import create_app
from travel_agent.api.streaming import result_card_from_tool_message


def test_attraction_lookup_and_uncollected_place_are_local_read_only():
    with TestClient(create_app(runtime_factory=FakeRuntime)) as client:
        response = client.post("/api/attractions/lookup", json=museum_place().model_dump())
        assert response.status_code == 200
        assert response.json()["card"]["name"] == "深圳博物馆金田路馆（历史民俗）"
        response = client.post("/api/attractions/lookup", json={
            **museum_place(provider_place_id="uncollected").model_dump(), "cost": 0,
        })
        assert response.json() == {"status": "not_found", "card": None}
        assert client.post("/api/attractions/lookup", json={}).status_code == 422


def test_chat_place_cards_keep_provider_id_and_city_for_attraction_lookup():
    payload = {
        "query": "深圳博物馆", "provider": "amap", "attribution": "高德地图",
        "places": [museum_place().model_dump()],
    }
    card = result_card_from_tool_message(ToolMessage(
        content=json.dumps(payload), name="search_places", tool_call_id="poi",
    ))
    assert card.provider == "amap"
    assert card.places[0].provider_place_id == "B02F38IRTR"
    assert card.places[0].city_code == "440300"
    payload.pop("provider")
    payload["places"][0].pop("provider_place_id")
    payload["places"][0].pop("city_code")
    legacy = result_card_from_tool_message(ToolMessage(
        content=json.dumps(payload), name="search_places", tool_call_id="legacy",
    ))
    assert legacy.provider is None
    assert legacy.places[0].provider_place_id is None
