import pytest
from pydantic import ValidationError

from travel_agent.domain.decision import (
    ScoreBreakdown,
    ScoredTravelOption,
    TravelOption,
    TravelRecommendation,
)


def build_available_option(mode: str = "transit") -> TravelOption:
    return TravelOption(
        mode=mode,
        distance_m=12_000,
        duration_s=2_700,
        walking_distance_m=600,
        cost_yuan=5,
        transfer_count=1,
        attribution="公交路线数据来源：高德地图 Web服务 API",
    )


def build_scores(total: float = 82) -> ScoreBreakdown:
    return ScoreBreakdown(
        time=80,
        cost=90,
        walking=75,
        transfers=80,
        weather_fit=85,
        total=total,
    )


def test_available_option_requires_route_facts_and_attribution() -> None:
    with pytest.raises(ValidationError):
        TravelOption(mode="walking")


def test_failed_option_preserves_failure_reason_without_fake_metrics() -> None:
    option = TravelOption(
        mode="driving",
        status="failed",
        failure_reason="驾车路线服务超时",
    )

    assert option.duration_s is None
    assert option.failure_reason == "驾车路线服务超时"


def test_failed_option_cannot_be_scored() -> None:
    option = TravelOption(
        mode="driving",
        status="failed",
        failure_reason="驾车路线服务超时",
    )

    with pytest.raises(ValidationError):
        ScoredTravelOption(option=option, scores=build_scores())


def test_recommendation_requires_recommended_mode_to_rank_first() -> None:
    transit = ScoredTravelOption(
        option=build_available_option("transit"),
        scores=build_scores(),
        reasons=["雨天步行距离较短"],
    )

    with pytest.raises(ValidationError):
        TravelRecommendation(
            recommended_mode="walking",
            ranked_options=[transit],
            confidence=0.85,
            summary_reasons=["公共交通综合得分最高"],
        )


def test_recommendation_keeps_ranked_and_unavailable_options() -> None:
    transit = ScoredTravelOption(
        option=build_available_option("transit"),
        scores=build_scores(),
        reasons=["雨天步行距离较短"],
    )
    driving_failure = TravelOption(
        mode="driving",
        status="failed",
        failure_reason="驾车路线服务超时",
    )

    result = TravelRecommendation(
        recommended_mode="transit",
        ranked_options=[transit],
        unavailable_options=[driving_failure],
        confidence=0.85,
        summary_reasons=["公共交通综合得分最高"],
        limitations=["驾车方案暂不可用"],
    )

    assert result.ranked_options[0].option.mode == "transit"
    assert result.unavailable_options[0].failure_reason == "驾车路线服务超时"
