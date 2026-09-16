from pathlib import Path

from travel_agent.config import ApiSettings, Settings


def test_settings_validate_values_and_hide_secret() -> None:
    settings = Settings(
        _env_file=None,
        dashscope_api_key="sk-test-only",
        dashscope_base_url="https://example.com/compatible-mode/v1",
        amap_api_key="amap-test-only",
    )

    assert settings.model_name == "qwen-plus"
    assert settings.place_proxy_url is None
    assert settings.route_proxy_url is None
    assert settings.checkpoint_storage_path == Path("data/checkpoints.sqlite")
    assert settings.conversation_storage_path == Path(
        "data/conversations.sqlite"
    )
    assert settings.business_timezone == "Asia/Shanghai"
    assert settings.route_snapshot_ttl_seconds == 300
    assert str(settings.dashscope_api_key) == "**********"
    assert settings.dashscope_api_key.get_secret_value() == "sk-test-only"
    assert str(settings.amap_api_key) == "**********"
    assert settings.amap_api_key.get_secret_value() == "amap-test-only"
    assert str(settings.dashscope_base_url).startswith("https://example.com/")


def test_api_settings_have_safe_local_cors_defaults() -> None:
    settings = ApiSettings(_env_file=None)

    assert settings.cors_allowed_origins == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
