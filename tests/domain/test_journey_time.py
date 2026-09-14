from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from travel_agent.domain.journey_time import ArrivalDeadline, DepartureTime

SHANGHAI = ZoneInfo("Asia/Shanghai")
REFERENCE_TIME = datetime(2026, 9, 11, 10, 0, tzinfo=SHANGHAI)


def build_departure(
    departure_at: datetime,
    *,
    precision: str = "exact",
    source_text: str = "明天下午三点",
) -> DepartureTime:
    return DepartureTime(
        departure_at=departure_at,
        timezone="Asia/Shanghai",
        precision=precision,
        source_text=source_text,
    )


def test_departure_time_normalizes_to_business_timezone() -> None:
    departure = build_departure(
        datetime(2026, 9, 12, 7, 0, tzinfo=UTC),
    )

    assert departure.departure_at == datetime(
        2026, 9, 12, 15, 0, tzinfo=SHANGHAI
    )
    assert departure.source_text == "明天下午三点"


def test_departure_time_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError, match="timezone information"):
        build_departure(datetime(2026, 9, 12, 15, 0))


def test_arrival_deadline_calculates_latest_departure_with_buffer() -> None:
    deadline = ArrivalDeadline(
        arrival_by=datetime(2026, 9, 14, 9, 0, tzinfo=SHANGHAI),
        timezone="Asia/Shanghai",
        precision="exact",
        source_text="周一早上9点前到",
        buffer_minutes=15,
    )

    assert deadline.latest_departure_at(35 * 60) == datetime(
        2026,
        9,
        14,
        8,
        10,
        tzinfo=SHANGHAI,
    )


def test_arrival_deadline_rejects_negative_duration() -> None:
    deadline = ArrivalDeadline(
        arrival_by=datetime(2026, 9, 14, 9, 0, tzinfo=SHANGHAI),
        precision="exact",
        source_text="周一早上9点前到",
    )

    with pytest.raises(ValueError, match="duration_s cannot be negative"):
        deadline.latest_departure_at(-1)


def test_departure_time_rejects_unknown_timezone() -> None:
    with pytest.raises(ValidationError, match="valid IANA timezone"):
        DepartureTime(
            departure_at=REFERENCE_TIME,
            timezone="Mars/Olympus",
            precision="exact",
            source_text="现在",
        )


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (timedelta(minutes=-16), "past"),
        (timedelta(minutes=-10), "immediate"),
        (timedelta(minutes=10), "immediate"),
        (timedelta(minutes=16), "future"),
    ],
)
def test_departure_relation_uses_explicit_reference_time(
    offset: timedelta,
    expected: str,
) -> None:
    departure = build_departure(REFERENCE_TIME + offset)

    assert departure.relation_to(REFERENCE_TIME) == expected


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (timedelta(minutes=-20), "past"),
        (timedelta(minutes=5), "current"),
        (timedelta(days=2), "forecast"),
        (timedelta(days=15), "forecast"),
        (timedelta(days=16), "out_of_range"),
    ],
)
def test_weather_query_kind_respects_time_and_forecast_horizon(
    offset: timedelta,
    expected: str,
) -> None:
    departure = build_departure(REFERENCE_TIME + offset)

    assert departure.weather_query_kind(REFERENCE_TIME) == expected
