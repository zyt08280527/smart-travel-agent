from collections.abc import Callable

from travel_agent.domain.decision import (
    ScoreBreakdown,
    ScoredTravelOption,
    TravelOption,
    TravelPreferences,
    TravelPriority,
    TravelRecommendation,
)
from travel_agent.domain.weather import ForecastWeather, WeatherData

MetricGetter = Callable[[TravelOption], float | None]

PRIORITY_WEIGHTS: dict[TravelPriority, dict[str, float]] = {
    "balanced": {
        "time": 0.32,
        "cost": 0.16,
        "walking": 0.20,
        "transfers": 0.12,
        "weather_fit": 0.20,
    },
    "fastest": {
        "time": 0.60,
        "cost": 0.08,
        "walking": 0.08,
        "transfers": 0.08,
        "weather_fit": 0.16,
    },
    "cheapest": {
        "time": 0.16,
        "cost": 0.52,
        "walking": 0.08,
        "transfers": 0.08,
        "weather_fit": 0.16,
    },
    "least_walking": {
        "time": 0.16,
        "cost": 0.08,
        "walking": 0.52,
        "transfers": 0.08,
        "weather_fit": 0.16,
    },
    "fewest_transfers": {
        "time": 0.16,
        "cost": 0.08,
        "walking": 0.08,
        "transfers": 0.52,
        "weather_fit": 0.16,
    },
}


class TravelDecisionError(ValueError):
    """Raised when no candidate can participate in route comparison."""


def _walking_distance(option: TravelOption) -> float | None:
    if option.walking_distance_m is not None:
        return option.walking_distance_m
    if option.mode == "walking":
        return option.distance_m
    if option.mode == "driving":
        return 0
    return None


def _transfer_count(option: TravelOption) -> float | None:
    if option.transfer_count is not None:
        return float(option.transfer_count)
    if option.mode != "transit":
        return 0
    return None


def _apply_hard_constraints(
    options: list[TravelOption],
    preferences: TravelPreferences,
) -> tuple[list[TravelOption], list[TravelOption], list[str]]:
    """Exclude routes that violate explicit user constraints."""
    available: list[TravelOption] = []
    unavailable = [option for option in options if option.status != "available"]
    limitations: list[str] = []

    for option in options:
        if option.status != "available":
            continue

        failure_reason: str | None = None
        if option.mode == "driving" and preferences.can_drive is False:
            failure_reason = "用户明确表示不能驾车"

        walking_distance = _walking_distance(option)
        if (
            failure_reason is None
            and preferences.max_walking_distance_m is not None
        ):
            if walking_distance is None:
                limitations.append(
                    f"{option.mode} 方案缺少步行距离，无法验证最大步行约束"
                )
            elif walking_distance > preferences.max_walking_distance_m:
                failure_reason = (
                    f"步行距离 {walking_distance:.0f} 米超过用户上限 "
                    f"{preferences.max_walking_distance_m:.0f} 米"
                )

        transfer_count = _transfer_count(option)
        if (
            failure_reason is None
            and option.mode == "transit"
            and preferences.max_transfer_count is not None
        ):
            if transfer_count is None:
                limitations.append("transit 方案缺少换乘次数，无法验证换乘约束")
            elif transfer_count > preferences.max_transfer_count:
                failure_reason = (
                    f"换乘 {transfer_count:.0f} 次超过用户上限 "
                    f"{preferences.max_transfer_count} 次"
                )

        if failure_reason is None:
            available.append(option)
        else:
            unavailable.append(
                option.model_copy(
                    update={
                        "status": "unavailable",
                        "failure_reason": failure_reason,
                    }
                )
            )

    return available, unavailable, limitations


def _lower_is_better_scores(
    options: list[TravelOption],
    getter: MetricGetter,
) -> dict[int, float]:
    """Normalize one metric to 0-100 without treating missing data as zero."""
    values = [getter(option) for option in options]
    known_values = [value for value in values if value is not None]
    if not known_values:
        return {index: 50.0 for index in range(len(options))}

    minimum = min(known_values)
    maximum = max(known_values)
    scores: dict[int, float] = {}
    for index, value in enumerate(values):
        if value is None:
            scores[index] = 50.0
        elif maximum == minimum:
            scores[index] = 100.0
        else:
            scores[index] = 100 * (maximum - value) / (maximum - minimum)
    return scores


def _weather_fit_score(
    option: TravelOption,
    weather: WeatherData | None,
) -> tuple[float, list[str]]:
    """Score how suitable one travel mode is for observed weather."""
    if weather is None:
        return 50.0, []

    base_scores = {"walking": 100.0, "transit": 90.0, "driving": 95.0}
    score = base_scores[option.mode]
    reasons: list[str] = []

    weather_time_label = "预计" if isinstance(weather, ForecastWeather) else "当前"
    precipitation_penalties = {
        "walking": (20, 40, 60),
        "transit": (6, 12, 20),
        "driving": (0, 3, 5),
    }
    if weather.precipitation_mm >= 5:
        score -= precipitation_penalties[option.mode][2]
        reasons.append(
            f"{weather_time_label}降水量 {weather.precipitation_mm:g} 毫米"
        )
    elif weather.precipitation_mm >= 1:
        score -= precipitation_penalties[option.mode][1]
        reasons.append(
            f"{weather_time_label}降水量 {weather.precipitation_mm:g} 毫米"
        )
    elif weather.precipitation_mm > 0:
        score -= precipitation_penalties[option.mode][0]
        reasons.append(
            f"{weather_time_label}存在少量降水 {weather.precipitation_mm:g} 毫米"
        )

    apparent_temperature = weather.apparent_temperature_c
    temperature_penalties = {
        "walking": (15, 30, 45),
        "transit": (5, 10, 15),
        "driving": (0, 2, 5),
    }
    if apparent_temperature <= 0 or apparent_temperature >= 38:
        score -= temperature_penalties[option.mode][2]
        reasons.append(f"体感温度为 {apparent_temperature:g}℃")
    elif apparent_temperature <= 5 or apparent_temperature >= 35:
        score -= temperature_penalties[option.mode][1]
        reasons.append(f"体感温度为 {apparent_temperature:g}℃")
    elif apparent_temperature <= 10 or apparent_temperature >= 32:
        score -= temperature_penalties[option.mode][0]
        reasons.append(f"体感温度为 {apparent_temperature:g}℃")

    wind_penalties = {
        "walking": (15, 30, 50),
        "transit": (5, 10, 15),
        "driving": (0, 5, 10),
    }
    if weather.wind_speed_kmh >= 50:
        score -= wind_penalties[option.mode][2]
        reasons.append(f"{weather_time_label}风速为 {weather.wind_speed_kmh:g} km/h")
    elif weather.wind_speed_kmh >= 30:
        score -= wind_penalties[option.mode][1]
        reasons.append(f"{weather_time_label}风速为 {weather.wind_speed_kmh:g} km/h")
    elif weather.wind_speed_kmh >= 20:
        score -= wind_penalties[option.mode][0]
        reasons.append(f"{weather_time_label}风速为 {weather.wind_speed_kmh:g} km/h")

    return max(0.0, round(score, 2)), reasons


def _confidence(ranked_options: list[ScoredTravelOption]) -> float:
    if len(ranked_options) == 1:
        return 0.4
    score_gap = ranked_options[0].scores.total - ranked_options[1].scores.total
    return round(min(0.9, 0.5 + max(score_gap, 0) / 100), 3)


def recommend_travel_mode(
    options: list[TravelOption],
    preferences: TravelPreferences | None = None,
    weather: WeatherData | None = None,
) -> TravelRecommendation:
    """Rank available routes using deterministic user-priority weights."""
    preferences = preferences or TravelPreferences()
    available, unavailable, limitations = _apply_hard_constraints(
        options,
        preferences,
    )
    if not available:
        reasons = "；".join(
            f"{option.mode}: {option.failure_reason}"
            for option in unavailable
            if option.failure_reason
        )
        message = "没有符合用户硬约束的出行方案"
        if reasons:
            message += f"：{reasons}"
        raise TravelDecisionError(message)

    metric_scores = {
        "time": _lower_is_better_scores(available, lambda item: item.duration_s),
        "cost": _lower_is_better_scores(available, lambda item: item.cost_yuan),
        "walking": _lower_is_better_scores(available, _walking_distance),
        "transfers": _lower_is_better_scores(available, _transfer_count),
    }
    weather_results = [
        _weather_fit_score(option, weather) for option in available
    ]
    metric_scores["weather_fit"] = {
        index: result[0] for index, result in enumerate(weather_results)
    }
    weights = PRIORITY_WEIGHTS[preferences.priority]

    scored: list[ScoredTravelOption] = []
    for index, option in enumerate(available):
        scores = {
            metric: round(values[index], 2)
            for metric, values in metric_scores.items()
        }
        total = round(
            sum(scores[metric] * weight for metric, weight in weights.items()),
            2,
        )
        reasons = [
            f"预计耗时 {option.duration_s:.0f} 秒",
            f"路线距离 {option.distance_m:.0f} 米",
        ]
        reasons.extend(weather_results[index][1])
        if option.cost_yuan is None:
            limitations.append(f"{option.mode} 方案缺少费用数据")
        scored.append(
            ScoredTravelOption(
                option=option,
                scores=ScoreBreakdown(
                    **scores,
                    total=total,
                ),
                reasons=reasons,
            )
        )

    ranked = sorted(scored, key=lambda item: item.scores.total, reverse=True)
    recommended_mode = ranked[0].option.mode
    if weather is None:
        limitations.append("未提供天气数据，本次使用中性天气适配分")
    limitations.extend(
        f"{option.mode} 方案未参与评分：{option.failure_reason}"
        for option in unavailable
    )
    return TravelRecommendation(
        recommended_mode=recommended_mode,
        ranked_options=ranked,
        unavailable_options=unavailable,
        confidence=_confidence(ranked),
        summary_reasons=[
            f"在 {preferences.priority} 偏好下，{recommended_mode} 综合得分最高"
        ],
        limitations=list(dict.fromkeys(limitations)),
    )
