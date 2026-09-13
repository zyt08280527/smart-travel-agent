from functools import lru_cache
from pathlib import Path

from pydantic import Field, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
)


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env."""

    model_config = ENV_FILE_CONFIG

    dashscope_api_key: SecretStr
    dashscope_base_url: HttpUrl
    amap_api_key: SecretStr
    model_name: str = Field(default="qwen-plus", min_length=1)
    place_proxy_url: HttpUrl | None = None
    route_proxy_url: HttpUrl | None = None
    itinerary_storage_path: Path = Path("data/itineraries.jsonl")
    checkpoint_storage_path: Path = Path("data/checkpoints.sqlite")
    conversation_storage_path: Path = Path("data/conversations.sqlite")
    business_timezone: str = Field(default="Asia/Shanghai", min_length=1)
    app_env: str = "development"
    log_level: str = "INFO"


class ApiSettings(BaseSettings):
    """HTTP boundary settings that do not require model credentials."""

    model_config = ENV_FILE_CONFIG

    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        min_length=1,
    )


@lru_cache
def get_settings() -> Settings:
    """Load and cache application settings."""
    return Settings()


@lru_cache
def get_api_settings() -> ApiSettings:
    """Load and cache HTTP boundary settings."""
    return ApiSettings()
