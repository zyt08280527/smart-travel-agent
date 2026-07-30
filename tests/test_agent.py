from travel_agent.agent import _itinerary_server_env


def test_itinerary_server_env_forwards_storage_override(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "ITINERARY_STORAGE_PATH",
        "temporary/eval-itineraries.jsonl",
    )

    assert _itinerary_server_env() == {
        "ITINERARY_STORAGE_PATH": "temporary/eval-itineraries.jsonl"
    }


def test_itinerary_server_env_is_none_without_override(monkeypatch) -> None:
    monkeypatch.delenv("ITINERARY_STORAGE_PATH", raising=False)

    assert _itinerary_server_env() is None
