from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from travel_agent.services.departure_time_parser import (
    ArrivalTimeParser,
    DepartureTimeParseError,
    DepartureTimeParser,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
REFERENCE_TIME = datetime(2026, 9, 11, 10, 20, tzinfo=SHANGHAI)  # Friday


@pytest.mark.parametrize(
    ("text", "expected", "precision"),
    [
        ("现在出发", datetime(2026, 9, 11, 10, 20, tzinfo=SHANGHAI), "now"),
        (
            "今天下午3点出发",
            datetime(2026, 9, 11, 15, 0, tzinfo=SHANGHAI),
            "exact",
        ),
        (
            "明天下午去丽湖校区",
            datetime(2026, 9, 12, 15, 0, tzinfo=SHANGHAI),
            "time_period",
        ),
        (
            "后天早上8点半出发",
            datetime(2026, 9, 13, 8, 30, tzinfo=SHANGHAI),
            "exact",
        ),
        (
            "三天后晚上七点出发",
            datetime(2026, 9, 14, 19, 0, tzinfo=SHANGHAI),
            "exact",
        ),
        (
            "周六早上出发",
            datetime(2026, 9, 12, 8, 0, tzinfo=SHANGHAI),
            "time_period",
        ),
        (
            "下周一15:30出发",
            datetime(2026, 9, 14, 15, 30, tzinfo=SHANGHAI),
            "exact",
        ),
        (
            "明天去丽湖校区",
            datetime(2026, 9, 12, 12, 0, tzinfo=SHANGHAI),
            "date_only",
        ),
    ],
)
def test_parse_common_chinese_departure_time(
    text: str,
    expected: datetime,
    precision: str,
) -> None:
    result = DepartureTimeParser().parse(text, reference_at=REFERENCE_TIME)

    assert result is not None
    assert result.departure_at == expected
    assert result.precision == precision
    assert result.source_text == text


def test_parse_returns_none_when_user_did_not_state_time() -> None:
    result = DepartureTimeParser().parse(
        "从粤海校区到丽湖校区怎么走？",
        reference_at=REFERENCE_TIME,
    )

    assert result is None


def test_parse_rejects_invalid_clock_time() -> None:
    with pytest.raises(DepartureTimeParseError, match="小时或分钟无效"):
        DepartureTimeParser().parse(
            "明天下午25点出发",
            reference_at=REFERENCE_TIME,
        )


def test_parse_rejects_naive_reference_time() -> None:
    with pytest.raises(DepartureTimeParseError, match="必须包含时区"):
        DepartureTimeParser().parse(
            "明天下午出发",
            reference_at=datetime(2026, 9, 11, 10, 20),
        )


def test_parse_arrival_deadline_preserves_user_expression_and_buffer() -> None:
    result = ArrivalTimeParser().parse(
        "下周一早上9点前到公司",
        reference_at=REFERENCE_TIME,
        buffer_minutes=20,
    )

    assert result is not None
    assert result.arrival_by == datetime(2026, 9, 14, 9, 0, tzinfo=SHANGHAI)
    assert result.precision == "exact"
    assert result.source_text == "下周一早上9点前到公司"
    assert result.buffer_minutes == 20
