from travel_agent.services.weather import weather_code_to_condition


def test_weather_code_95_is_thunderstorm() -> None:
    assert weather_code_to_condition(95) == "雷暴"


def test_unknown_weather_code_remains_explainable() -> None:
    assert weather_code_to_condition(999) == "未知天气（WMO代码：999）"
