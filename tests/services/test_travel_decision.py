import pytest

from travel_agent.domain.decision import TravelOption, TravelPreferences
from travel_agent.domain.weather import CurrentWeather, ForecastWeather, Location
from travel_agent.services.travel_decision import (
    RECOMMENDATION_VARIANT_PRIORITIES,
    TravelDecisionError,
    recommend_travel_mode,
    recommend_travel_variants,
)


def build_options() -> list[TravelOption]:
    return [
        TravelOption(
            mode="walking",
            distance_m=5_000,
            duration_s=3_600,
            walking_distance_m=5_000,
            cost_yuan=0,
            transfer_count=0,
            attribution="walking provider",
        ),
        TravelOption(
            mode="transit",
            distance_m=8_000,
            duration_s=2_400,
            walking_distance_m=500,
            cost_yuan=5,
            transfer_count=1,
            attribution="transit provider",
        ),
        TravelOption(
            mode="driving",
            distance_m=10_000,
            duration_s=1_800,
            walking_distance_m=0,
            cost_yuan=20,
            transfer_count=0,
            attribution="driving provider",
        ),
    ]


def build_weather(
    *,
    apparent_temperature_c: float = 25,
    precipitation_mm: float = 0,
    wind_speed_kmh: float = 8,
) -> CurrentWeather:
    return CurrentWeather(
        location=Location(
            name="深圳",
            country="中国",
            admin1="广东",
            latitude=22.54,
            longitude=114.06,
        ),
        temperature_c=25,
        apparent_temperature_c=apparent_temperature_c,
        precipitation_mm=precipitation_mm,
        wind_speed_kmh=wind_speed_kmh,
        weather_code=0,
        condition="晴",
        observed_at="2026-09-11T10:00",
    )


def build_forecast_weather() -> ForecastWeather:
    return ForecastWeather(
        location=Location(
            name="深圳",
            country="中国",
            admin1="广东",
            latitude=22.54,
            longitude=114.06,
        ),
        temperature_c=30,
        apparent_temperature_c=34,
        precipitation_mm=2,
        wind_speed_kmh=8,
        weather_code=61,
        condition="小雨",
        forecast_at="2026-09-12T15:00",
    )


def test_fastest_priority_recommends_driving() -> None:
    result = recommend_travel_mode(
        build_options(),
        TravelPreferences(priority="fastest"),
    )

    assert result.recommended_mode == "driving"
    assert result.ranked_options[0].scores.total > result.ranked_options[1].scores.total


def test_cheapest_priority_recommends_walking() -> None:
    result = recommend_travel_mode(
        build_options(),
        TravelPreferences(priority="cheapest"),
    )

    assert result.recommended_mode == "walking"


def test_recommendation_variants_reuse_constraints_for_every_priority() -> None:
    variants = recommend_travel_variants(
        build_options(),
        TravelPreferences(can_drive=False),
        weather=build_weather(),
    )

    assert [variant.priority for variant in variants] == list(
        RECOMMENDATION_VARIANT_PRIORITIES
    )
    assert len(variants) == 5
    assert all(
        scored.option.mode != "driving"
        for variant in variants
        for scored in variant.ranked_options
    )
    assert variants[0].recommended_mode == variants[0].ranked_options[0].option.mode


def test_missing_cost_is_penalized_when_cost_is_the_primary_priority() -> None:
    options = build_options()
    options[1] = options[1].model_copy(update={"cost_yuan": None})

    result = recommend_travel_mode(
        options,
        TravelPreferences(priority="cheapest"),
    )

    transit = next(
        item for item in result.ranked_options if item.option.mode == "transit"
    )
    assert transit.scores.cost == 0
    assert "transit 方案缺少费用数据" in result.limitations


def test_missing_cost_remains_neutral_for_balanced_recommendations() -> None:
    options = build_options()
    options[1] = options[1].model_copy(update={"cost_yuan": None})

    result = recommend_travel_mode(options)

    transit = next(
        item for item in result.ranked_options if item.option.mode == "transit"
    )
    assert transit.scores.cost == 50


def test_failed_option_is_preserved_but_not_scored() -> None:
    options = build_options()
    options[2] = TravelOption(
        mode="driving",
        status="failed",
        failure_reason="驾车服务超时",
    )

    result = recommend_travel_mode(options)

    assert all(
        item.option.mode != "driving" for item in result.ranked_options
    )
    assert result.unavailable_options[0].mode == "driving"
    assert "driving 方案未参与评分：驾车服务超时" in result.limitations


def test_no_available_option_is_rejected() -> None:
    with pytest.raises(
        TravelDecisionError,
        match="没有符合用户硬约束的出行方案.*公交服务超时",
    ):
        recommend_travel_mode(
            [
                TravelOption(
                    mode="transit",
                    status="failed",
                    failure_reason="公交服务超时",
                )
            ]
        )


def test_conflicting_constraints_report_every_excluded_mode() -> None:
    with pytest.raises(TravelDecisionError) as exc_info:
        recommend_travel_mode(
            build_options(),
            TravelPreferences(
                can_drive=False,
                max_walking_distance_m=100,
                max_transfer_count=0,
            ),
        )

    message = str(exc_info.value)
    assert "没有符合用户硬约束的出行方案" in message
    assert "walking: 步行距离 5000 米超过用户上限 100 米" in message
    assert "transit: 步行距离 500 米超过用户上限 100 米" in message
    assert "driving: 用户明确表示不能驾车" in message


def test_cannot_drive_excludes_driving_option() -> None:
    result = recommend_travel_mode(
        build_options(),
        TravelPreferences(priority="fastest", can_drive=False),
    )

    assert result.recommended_mode == "transit"
    excluded = next(
        option for option in result.unavailable_options if option.mode == "driving"
    )
    assert excluded.failure_reason == "用户明确表示不能驾车"


def test_maximum_walking_distance_excludes_long_walking_routes() -> None:
    result = recommend_travel_mode(
        build_options(),
        TravelPreferences(max_walking_distance_m=1_000),
    )

    assert all(
        item.option.mode != "walking" for item in result.ranked_options
    )
    excluded = next(
        option for option in result.unavailable_options if option.mode == "walking"
    )
    assert "超过用户上限" in (excluded.failure_reason or "")


def test_maximum_transfer_count_excludes_transit_route() -> None:
    result = recommend_travel_mode(
        build_options(),
        TravelPreferences(max_transfer_count=0),
    )

    assert all(
        item.option.mode != "transit" for item in result.ranked_options
    )
    excluded = next(
        option for option in result.unavailable_options if option.mode == "transit"
    )
    assert excluded.failure_reason == "换乘 1 次超过用户上限 0 次"


def test_unknown_transit_walking_distance_is_reported_not_faked() -> None:
    options = build_options()
    options[1] = options[1].model_copy(update={"walking_distance_m": None})

    result = recommend_travel_mode(
        options,
        TravelPreferences(max_walking_distance_m=1_000),
    )

    assert any(
        item.option.mode == "transit" for item in result.ranked_options
    )
    assert (
        "transit 方案缺少步行距离，无法验证最大步行约束"
        in result.limitations
    )


def test_heavy_rain_penalizes_walking_more_than_transit() -> None:
    equal_options = [
        TravelOption(
            mode="walking",
            distance_m=2_000,
            duration_s=1_200,
            walking_distance_m=1_000,
            cost_yuan=0,
            transfer_count=0,
            attribution="walking provider",
        ),
        TravelOption(
            mode="transit",
            distance_m=2_000,
            duration_s=1_200,
            walking_distance_m=1_000,
            cost_yuan=0,
            transfer_count=0,
            attribution="transit provider",
        ),
    ]

    result = recommend_travel_mode(
        equal_options,
        weather=build_weather(precipitation_mm=5),
    )

    assert result.recommended_mode == "transit"
    scores = {
        item.option.mode: item.scores.weather_fit
        for item in result.ranked_options
    }
    assert scores["transit"] > scores["walking"]


def test_forecast_weather_reasons_are_not_described_as_current() -> None:
    result = recommend_travel_mode(
        build_options(),
        weather=build_forecast_weather(),
    )

    reasons = [
        reason
        for item in result.ranked_options
        for reason in item.reasons
    ]
    assert any("预计降水量" in reason for reason in reasons)
    assert all("当前降水量" not in reason for reason in reasons)


def test_extreme_temperature_reduces_walking_weather_fit() -> None:
    result = recommend_travel_mode(
        build_options(),
        weather=build_weather(apparent_temperature_c=39),
    )

    walking = next(
        item for item in result.ranked_options if item.option.mode == "walking"
    )
    driving = next(
        item for item in result.ranked_options if item.option.mode == "driving"
    )
    assert walking.scores.weather_fit == 55
    assert driving.scores.weather_fit == 90


def test_missing_weather_uses_neutral_score_and_reports_limitation() -> None:
    result = recommend_travel_mode(build_options())

    assert all(
        item.scores.weather_fit == 50 for item in result.ranked_options
    )
    assert "未提供天气数据，本次使用中性天气适配分" in result.limitations
