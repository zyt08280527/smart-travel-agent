from langchain_openai import ChatOpenAI

from travel_agent.config import Settings, get_settings


def create_chat_model(settings: Settings | None = None) -> ChatOpenAI:
    """Create the configured OpenAI-compatible Qwen chat model."""
    app_settings = settings or get_settings()
    return ChatOpenAI(
        model=app_settings.model_name,
        api_key=app_settings.dashscope_api_key,
        base_url=str(app_settings.dashscope_base_url),
        temperature=0,
        timeout=30.0,
        max_retries=2,
    )

