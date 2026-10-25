from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from travel_agent.domain.itinerary import ItineraryDraft
from travel_agent.domain.multi_stop_weather import MultiStopWeatherReport, StopWeather
from travel_agent.domain.route import RoutePlan
from travel_agent.services.departure_watch import DepartureWatch
from travel_agent.services.itinerary import ItineraryService
from travel_agent.services.multi_stop_workspace import MultiStopWorkspace
from travel_agent.services.single_departure_watch import SingleDepartureChecker, SingleWatchSnapshot

NOW = datetime(2030, 1, 1, tzinfo=UTC)


async def saved_trip(tmp_path, mode='walking', context=True):
    service = ItineraryService(tmp_path / 'single.jsonl')
    saved = await service.save(ItineraryDraft(
        title='单段', origin='起点', destination='终点', travel_mode=mode,
        distance_m=2000, duration_s=1200,
        watch_context={'city': '深圳', 'origin_name': '起点', 'destination_name': '终点',
                       'origin': {'latitude': 22.5, 'longitude': 114},
                       'destination': {'latitude': 22.6, 'longitude': 114.1}}
        if context else None,
    ))
    return service, saved


@pytest.mark.asyncio
async def test_single_opt_in_time_restart_and_no_saved_mutation(tmp_path):
    storage, saved = await saved_trip(tmp_path)
    workspace = MultiStopWorkspace(tmp_path / 'watch.sqlite')
    checker = AsyncMock()
    checker.check.return_value = '检查提示'
    service = DepartureWatch(workspace, AsyncMock(), AsyncMock(), checker)
    with pytest.raises(ValueError, match='出发时间'):
        await service.enable_single(saved, now=NOW)
    row = await service.enable_single(saved, NOW + timedelta(hours=1), now=NOW)
    assert row['saved_id'] == 'single:' + saved.itinerary_id
    assert (await service.enable_single(saved, NOW + timedelta(hours=1), now=NOW)) == row
    await service.tick(now=NOW)
    checker.check.assert_not_awaited()
    await DepartureWatch(workspace, AsyncMock(), AsyncMock(), checker).tick(
        now=NOW + timedelta(minutes=40))
    assert (await service.list())[0]['status'] == 'completed'
    assert (await storage.get(saved.itinerary_id)) == saved


@pytest.mark.asyncio
async def test_legacy_rejected_and_delete_cancel_blocks_inflight_result(tmp_path):
    _, legacy = await saved_trip(tmp_path, context=False)
    workspace = MultiStopWorkspace(tmp_path / 'watch.sqlite')
    service = DepartureWatch(workspace, AsyncMock(), AsyncMock(), AsyncMock())
    with pytest.raises(ValueError, match='旧记录'):
        await service.enable_single(legacy, NOW + timedelta(hours=1), now=NOW)
    _, saved = await saved_trip(tmp_path)
    row = await service.enable_single(saved, NOW + timedelta(minutes=20), now=NOW)
    assert service._claim(NOW)
    await service.cancel_saved_single(saved.itinerary_id)
    service._finish(row['id'], 'completed', '过时结果', NOW)
    assert (await service.list())[0]['status'] == 'cancelled'


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['walking', 'driving', 'transit'])
async def test_same_mode_refresh_weather_time_and_duration_notice(tmp_path, mode):
    _, saved = await saved_trip(tmp_path, mode)
    context = saved.watch_context
    routes, weather = AsyncMock(), AsyncMock()
    route = RoutePlan(mode=mode if mode != 'transit' else 'walking', origin=context.origin,
                      destination=context.destination, distance_m=3000, duration_s=2400,
                      attribution='高德')
    routes.plan_walking_route.return_value = route
    routes.plan_driving_route.return_value = route
    if mode == 'transit':
        from travel_agent.domain.transit import TransitLeg, TransitOption, TransitPlan

        routes.plan_transit_route.return_value = TransitPlan(
            origin=context.origin, destination=context.destination, origin_city_code='0755',
            destination_city_code='0755', attribution='高德', options=[TransitOption(
                distance_m=3000, duration_s=2400, walking_distance_m=300, transfer_count=1,
                legs=[TransitLeg(mode='bus', distance_m=2700)],
            )],
        )
    async def report(plan_id, points, *, now):
        return MultiStopWeatherReport(
            plan_id=plan_id, queried_at=now,
            stops=[StopWeather(stop_id=p[0], name=p[1], target_at=p[3], status='forecast')
                   for p in points], limitations=[],
        )
    weather.report_points.side_effect = report
    checker = SingleDepartureChecker(weather, walking=routes, driving=routes, transit=routes)
    snapshot = SingleWatchSnapshot(saved=saved, departure_at=NOW + timedelta(hours=1))
    text = await checker.check(snapshot, NOW)
    assert '增加约20分钟' in text
    points = weather.report_points.await_args.args[1]
    assert points[1][3] == snapshot.departure_at + timedelta(seconds=2400)
    weather.report_points.side_effect = RuntimeError('unavailable')
    with pytest.raises(RuntimeError):
        await checker.check(snapshot, NOW)
    if mode == 'transit':
        saved.watch_context.preferences.max_walking_distance_m = 100
        with pytest.raises(ValueError, match='原条件'):
            await checker.check(snapshot, NOW)
