"""Deterministic Chinese departure-time parsing for common travel requests."""

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from travel_agent.domain.journey_time import DeparturePrecision, DepartureTime

_PERIOD_DEFAULT_HOURS = {
    "凌晨": 1,
    "早上": 8,
    "早晨": 8,
    "上午": 9,
    "中午": 12,
    "下午": 15,
    "傍晚": 18,
    "晚上": 19,
    "今晚": 19,
}
_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_WEEKDAYS = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
}
_PERIOD_PATTERN = "|".join(_PERIOD_DEFAULT_HOURS)
_HOUR_PATTERN = r"\d{1,2}|[零〇一二两三四五六七八九十]{1,3}"


class DepartureTimeParseError(ValueError):
    """Raised when a time expression is present but cannot be used safely."""


def _parse_chinese_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if "十" in value:
        tens, ones = value.split("十", maxsplit=1)
        tens_value = _CHINESE_DIGITS.get(tens, 1) if tens else 1
        ones_value = _CHINESE_DIGITS.get(ones, 0) if ones else 0
        return tens_value * 10 + ones_value
    if len(value) == 1 and value in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[value]
    raise DepartureTimeParseError(f"无法识别时间数字：{value}")


def _apply_period(hour: int, period: str | None) -> int:
    if period in {"下午", "傍晚", "晚上", "今晚"} and 1 <= hour < 12:
        return hour + 12
    if period == "中午" and 1 <= hour < 11:
        return hour + 12
    if period == "凌晨" and hour == 12:
        return 0
    return hour


class DepartureTimeParser:
    """Parse high-frequency Chinese time expressions without an LLM call."""

    def __init__(self, timezone: str = "Asia/Shanghai") -> None:
        try:
            self._timezone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise DepartureTimeParseError("无法识别业务时区") from exc
        self._timezone_name = timezone

    def parse(
        self,
        text: str,
        *,
        reference_at: datetime,
    ) -> DepartureTime | None:
        """Return a normalized departure time, or None when no time is stated."""
        if reference_at.tzinfo is None or reference_at.utcoffset() is None:
            raise DepartureTimeParseError("reference_at 必须包含时区")
        normalized_text = text.strip()
        if not normalized_text:
            return None

        local_reference = reference_at.astimezone(self._timezone)
        if re.search(r"现在|马上|立刻|立即", normalized_text):
            return DepartureTime(
                departure_at=local_reference,
                timezone=self._timezone_name,
                precision="now",
                source_text=normalized_text,
            )

        target_date = self._parse_date(normalized_text, local_reference)
        parsed_time = self._parse_time(normalized_text)
        period = self._find_period(normalized_text)

        if target_date is None and parsed_time is None and period is None:
            return None
        if target_date is None:
            target_date = local_reference.date()

        if parsed_time is not None:
            target_time = parsed_time
            precision: DeparturePrecision = "exact"
        elif period is not None:
            target_time = time(hour=_PERIOD_DEFAULT_HOURS[period])
            precision = "time_period"
        else:
            target_time = time(hour=12)
            precision = "date_only"

        return DepartureTime(
            departure_at=datetime.combine(
                target_date,
                target_time,
                tzinfo=self._timezone,
            ),
            timezone=self._timezone_name,
            precision=precision,
            source_text=normalized_text,
        )

    def _parse_date(self, text: str, reference_at: datetime) -> date | None:
        if "后天" in text:
            return reference_at.date() + timedelta(days=2)
        if "明天" in text or "明早" in text or "明晚" in text:
            return reference_at.date() + timedelta(days=1)
        if "今天" in text or "今晚" in text:
            return reference_at.date()

        days_later = re.search(
            r"(\d+|[一二两三四五六七八九十]+)天后",
            text,
        )
        if days_later:
            days = _parse_chinese_number(days_later.group(1))
            return reference_at.date() + timedelta(days=days)

        weekday_match = re.search(r"(下周|这周|本周)?(?:周|星期)([一二三四五六日天])", text)
        if not weekday_match:
            return None
        prefix, weekday_text = weekday_match.groups()
        target_weekday = _WEEKDAYS[weekday_text]
        if prefix == "下周":
            days = 7 - reference_at.weekday() + target_weekday
        elif prefix in {"这周", "本周"}:
            days = target_weekday - reference_at.weekday()
        else:
            days = (target_weekday - reference_at.weekday()) % 7
        return reference_at.date() + timedelta(days=days)

    def _parse_time(self, text: str) -> time | None:
        period = self._find_period(text)
        colon_match = re.search(r"(?<!\d)(\d{1,2}):([0-5]\d)(?!\d)", text)
        if colon_match:
            hour = int(colon_match.group(1))
            minute = int(colon_match.group(2))
            return self._validated_time(_apply_period(hour, period), minute)

        hour_match = re.search(
            rf"({_HOUR_PATTERN})\s*[点时](?:(半)|(\d{{1,2}})\s*分?)?",
            text,
        )
        if not hour_match:
            return None
        hour = _parse_chinese_number(hour_match.group(1))
        minute = 30 if hour_match.group(2) else int(hour_match.group(3) or 0)
        return self._validated_time(_apply_period(hour, period), minute)

    @staticmethod
    def _find_period(text: str) -> str | None:
        match = re.search(_PERIOD_PATTERN, text)
        if match:
            return match.group(0)
        if "明早" in text:
            return "早上"
        if "明晚" in text:
            return "晚上"
        return None

    @staticmethod
    def _validated_time(hour: int, minute: int) -> time:
        try:
            return time(hour=hour, minute=minute)
        except ValueError as exc:
            raise DepartureTimeParseError("出发时间的小时或分钟无效") from exc
