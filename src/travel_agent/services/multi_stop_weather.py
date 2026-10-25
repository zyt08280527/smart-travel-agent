"""On-demand hourly point forecasts for a trusted selected plan; no reranking."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from travel_agent.domain.multi_stop import MultiStopDraft
from travel_agent.domain.multi_stop_weather import MultiStopWeatherReport, StopWeather
from travel_agent.domain.route import GeoPoint
from travel_agent.observability.http import observed_request
from travel_agent.services.weather import FORECAST_URL, weather_code_to_condition

SHANGHAI = ZoneInfo("Asia/Shanghai")


class MultiStopWeatherService:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._external_client = client

    async def report(
        self, draft: MultiStopDraft, plan_id: str, *, now: datetime | None = None,
    ) -> MultiStopWeatherReport:
        current = now or datetime.now(UTC)
        if current.utcoffset() is None:
            raise ValueError("天气查询时间必须包含时区")
        plan = next((p for p in draft.result.selection.plans if p.plan_id == plan_id), None)
        if plan is None:
            raise ValueError("所选方案不属于当前可信草案")
        if draft.result.request.departure_time is None:
            raise ValueError("请先提供出发时间并重新规划，再查询各站预计到达时的天气")
        locations = {stop.stop_id: stop for stop in draft.result.request.stops}
        return await self.report_points(plan_id, [
            (timing.stop_id, timing.name, locations[timing.stop_id].point, timing.arrival_at)
            for timing in plan.evaluation.stops
        ], now=current)

    async def report_points(
        self, plan_id: str, points: list[tuple[str, str, GeoPoint, datetime]], *, now=None,
    ) -> MultiStopWeatherReport:
        """Internal trusted-coordinate entry shared by single and multi-stop checks."""
        current = now or datetime.now(UTC)
        if current.utcoffset() is None or not 1 <= len(points) <= 5:
            raise ValueError("天气采样须有时区，且为1至5个地点")
        records = []
        groups: dict[tuple[float, float, str], list[int]] = {}
        last_date = current.astimezone(SHANGHAI).date() + timedelta(days=15)
        for stop_id, name, point, target in points:
            record = StopWeather(
                stop_id=stop_id, name=name, target_at=target, status="unavailable",
            )
            records.append(record)
            if target < current:
                record.status = "past"
                record.message = "该站预计到达时间已过去，本入口不提供历史天气。"
            elif target.astimezone(SHANGHAI).date() > last_date:
                record.status = "out_of_range"
                record.message = "超出本入口最多16天预报查询窗口；实际覆盖以返回数据为准。"
            else:
                key = (
                    point.latitude, point.longitude, target.astimezone(SHANGHAI).date().isoformat(),
                )
                groups.setdefault(key, []).append(len(records) - 1)

        async def populate(client: httpx.AsyncClient) -> None:
            semaphore = asyncio.Semaphore(2)

            async def fetch(key, indexes):
                latitude, longitude, day = key
                end = min(datetime.fromisoformat(day).date() + timedelta(days=1), last_date)
                try:
                    async with semaphore:
                        response = await observed_request(
                            client, "GET", FORECAST_URL, provider="open-meteo-multi-stop",
                            params={
                                "latitude": latitude, "longitude": longitude,
                                "hourly": "temperature_2m,apparent_temperature,precipitation,"
                                          "weather_code,wind_speed_10m",
                                "timezone": "Asia/Shanghai", "timeformat": "unixtime",
                                "temperature_unit": "celsius", "wind_speed_unit": "kmh",
                                "precipitation_unit": "mm", "start_date": day,
                                "end_date": end.isoformat(),
                            }, timeout=8,
                        )
                        response.raise_for_status()
                        payload = response.json()
                    for index in indexes:
                        try:
                            records[index] = self._match(payload, records[index])
                        except (KeyError, TypeError, ValueError, IndexError, OverflowError):
                            records[index].message = "预报未覆盖相近时刻或数据不完整，天气未知。"
                except (httpx.HTTPError, ValueError, TypeError):
                    for index in indexes:
                        records[index].message = "天气查询暂时失败，不能据此判断无雨或适合户外。"

            try:
                async with asyncio.timeout(25):
                    await asyncio.gather(*(fetch(key, indexes) for key, indexes in groups.items()))
            except TimeoutError:
                for record in records:
                    if record.status == "unavailable" and record.message is None:
                        record.message = "天气查询超时，请稍后重新查询。"

        if groups:
            if self._external_client is not None:
                await populate(self._external_client)
            else:
                async with httpx.AsyncClient(timeout=8) as client:
                    await populate(client)
        return MultiStopWeatherReport(
            plan_id=plan_id, queried_at=current, stops=records, limitations=[
                "按当前所选方案逐站预计到达时刻匹配相近小时预报（相差不超过30分钟）。",
                "这里只描述站点附近及对应小时，不覆盖整段路线、等待或全部游览时段。",
                "站点坐标用于匹配周边预报网格，不是该站点的实测气象数据。",
                "降水为预报时刻前一小时累计量，不是瞬时降雨量或降雨概率。",
                "预报是模型估计；提示阈值为产品规则，不是官方气象预警或安全保证。",
                "天气提示不参与当前路线排序，不修改方案、时间线或已保存记录。",
            ],
        )

    @staticmethod
    def _match(payload: dict[str, Any], base: StopWeather) -> StopWeather:
        units = payload["hourly_units"]
        for name, expected in {
            "temperature_2m": "°C", "apparent_temperature": "°C",
            "precipitation": "mm", "wind_speed_10m": "km/h",
        }.items():
            if units[name] != expected:
                raise ValueError("预报单位不符")
        hourly = payload["hourly"]
        times = [datetime.fromtimestamp(value, UTC) for value in hourly["time"]]
        if not times:
            raise ValueError("缺少小时预报")
        index = min(range(len(times)), key=lambda i: (abs(times[i] - base.target_at), times[i]))
        if abs(times[index] - base.target_at) > timedelta(minutes=30):
            raise ValueError("预报时刻距离过大")
        data = base.model_dump()
        code = hourly["weather_code"][index]
        if not isinstance(code, int):
            raise ValueError("天气代码缺失")
        data.update(
            status="forecast", forecast_at=times[index],
            temperature_c=hourly["temperature_2m"][index],
            apparent_temperature_c=hourly["apparent_temperature"][index],
            precipitation_mm=hourly["precipitation"][index],
            wind_speed_kmh=hourly["wind_speed_10m"][index],
            condition=weather_code_to_condition(code),
        )
        if any(data[name] is None for name in (
            "temperature_c", "apparent_temperature_c", "precipitation_mm", "wind_speed_kmh",
        )):
            raise ValueError("天气指标缺失")
        result = StopWeather.model_validate(data)
        if code in {95, 96, 99}:
            result.warnings.append("预报含雷暴信号，出发前核对当地官方天气信息。")
        if result.precipitation_mm > 0:
            result.warnings.append("对应小时有降水信号，请留意户外及步行安排。")
        if result.wind_speed_kmh >= 40:
            result.warnings.append("预报风速较高，请留意户外安排。")
        if result.apparent_temperature_c >= 35:
            result.warnings.append("预报体感温度较高，可考虑减少户外暴露。")
        return result
