from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from tests.api.test_app import FakeRuntime
from travel_agent.api.app import create_app
from travel_agent.domain.itinerary import SavedItinerary
from travel_agent.services.itinerary import ItineraryService
from travel_agent.services.multi_stop_workspace import MultiStopWorkspace


def test_opt_in_reopen_cancel_delete_and_legacy_rejection(tmp_path):
    storage = ItineraryService(tmp_path / 'saved.jsonl')
    workspace = MultiStopWorkspace(tmp_path / 'multi.sqlite')
    record = SavedItinerary(
        title='单段', origin='A', destination='B', travel_mode='walking', distance_m=1000,
        duration_s=900, itinerary_id='single-test', saved_at=datetime.now(UTC),
        watch_context={'city': '深圳', 'origin_name': 'A', 'destination_name': 'B',
                       'origin': {'latitude': 22.5, 'longitude': 114},
                       'destination': {'latitude': 22.6, 'longitude': 114.1}},
    )
    storage._append_record(record)
    checker = AsyncMock()
    def app():
        return create_app(runtime_factory=FakeRuntime, itinerary_service_factory=lambda: storage,
                          multi_stop_workspace_factory=lambda: workspace,
                          single_watch_checker_factory=lambda: checker)
    endpoint = '/api/itineraries/single-test/watch'
    with TestClient(app()) as client:
        assert client.post(endpoint, json={}).status_code == 422
        response = client.post(endpoint, json={
            'departure_at': (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        })
        assert response.status_code == 200
        identity = response.json()['id']
        assert client.get('/api/itineraries').json()[0]['watch_context']['origin']
    with TestClient(app()) as client:
        assert client.get('/api/departure-watches').json()[0]['id'] == identity
        assert client.delete('/api/itineraries/single-test').status_code == 204
        assert client.get('/api/departure-watches').json()[0]['status'] == 'cancelled'
        assert client.post(endpoint, json={}).status_code == 404
    checker.check.assert_not_awaited()
