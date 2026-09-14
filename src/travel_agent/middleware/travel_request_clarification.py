"""Pre-model clarification for incomplete travel requests."""

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, HumanMessage

from travel_agent.services.departure_time_parser import DepartureTimeParser

Clock = Callable[[], datetime]
_TRAVEL_INTENT_PATTERN = re.compile(
    r"出发|去往|前往|去[^天气]|路线|交通方式|怎么走|如何去|规划行程"
)
_ARRIVAL_INTENT_PATTERN = re.compile(
    r"最晚|不迟于|(?:点|时|:)\s*(?:前|之前)|"
    r"(?:前|之前)\s*(?:到|到达)|(?:到|到达).*?(?:前|之前)"
)
_CLARIFICATION = (
    "请补充具体出发时段，例如上午、下午、晚上，或直接告诉我几点出发。"
)
_ARRIVAL_CLARIFICATION = "请补充具体最晚到达时间，例如明天上午9点前到达。"


class TravelRequestClarificationMiddleware(AgentMiddleware):
    """Stop incomplete dated travel plans before model or external tool calls."""

    def __init__(
        self,
        timezone: str = "Asia/Shanghai",
        *,
        clock: Clock | None = None,
    ) -> None:
        self._timezone = timezone
        self._zone = ZoneInfo(timezone)
        self._clock = clock or (lambda: datetime.now(self._zone))
        self._parser = DepartureTimeParser(timezone)

    @hook_config(can_jump_to=["end"])
    def before_model(
        self,
        state: AgentState[Any],
        runtime: object,
    ) -> dict[str, Any] | None:
        """Return one focused clarification before any work starts this turn."""
        del runtime
        messages = state.get("messages", [])
        latest_human_index = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if isinstance(messages[index], HumanMessage)
            ),
            None,
        )
        if latest_human_index is None or latest_human_index != len(messages) - 1:
            return None

        content = messages[latest_human_index].content
        if not isinstance(content, str) or not (
            _TRAVEL_INTENT_PATTERN.search(content)
            or _ARRIVAL_INTENT_PATTERN.search(content)
        ):
            return None

        departure = self._parser.parse(
            content,
            reference_at=self._clock().astimezone(self._zone),
        )
        if departure is None or departure.precision != "date_only":
            return None
        clarification = (
            _ARRIVAL_CLARIFICATION
            if _ARRIVAL_INTENT_PATTERN.search(content)
            else _CLARIFICATION
        )
        return {
            "jump_to": "end",
            "messages": [AIMessage(content=clarification)],
        }
