"""Send one minimal request to verify the configured chat model connection."""

import asyncio

from travel_agent.config import get_settings
from travel_agent.model import create_chat_model


async def main() -> None:
    settings = get_settings()
    model = create_chat_model(settings)

    response = await model.ainvoke(
        [
            ("system", "你是连接测试助手。严格按照用户要求简短回答。"),
            ("human", "只回复四个字：连接成功"),
        ]
    )

    print(f"configured model: {settings.model_name}")
    print(f"response: {response.content}")
    print(f"token usage: {response.usage_metadata}")


if __name__ == "__main__":
    asyncio.run(main())
