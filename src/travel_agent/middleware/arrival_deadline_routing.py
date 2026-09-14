"""Keep complete arrival-deadline requests on the composite planning workflow."""

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import HumanMessage, ToolMessage

_ARRIVAL_DEADLINE_PATTERN = re.compile(
    r"最晚|不迟于|(?:点|时|:)\s*(?:前|之前)|"
    r"(?:前|之前)\s*(?:到|到达)|(?:到|到达).*?(?:前|之前)"
)
_ROUTE_PATTERN = re.compile(r"从.+(?:到|去往|前往).+|路线|交通方式|怎么走|如何去|规划")


def _tool_failed(message: ToolMessage) -> bool:
    content = message.content
    if not isinstance(content, str):
        return False
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return False
    return isinstance(payload, dict) and payload.get("ok") is False


class ArrivalDeadlineRoutingMiddleware(AgentMiddleware):
    """Force required tool stages for explicit latest-arrival route requests."""

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        messages = request.messages
        latest_human_index = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if isinstance(messages[index], HumanMessage)
            ),
            None,
        )
        if latest_human_index is None:
            return await handler(request)
        content = messages[latest_human_index].content
        if not isinstance(content, str) or not (
            _ARRIVAL_DEADLINE_PATTERN.search(content)
            and _ROUTE_PATTERN.search(content)
        ):
            return await handler(request)

        turn_messages = messages[latest_human_index + 1 :]
        planning_messages = [
            message
            for message in turn_messages
            if isinstance(message, ToolMessage)
            and message.name == "recommend_travel_plan"
        ]
        if planning_messages:
            return await handler(request)

        endpoint_messages = [
            message
            for message in turn_messages
            if isinstance(message, ToolMessage)
            and message.name == "resolve_route_endpoints"
        ]
        tool_name = "resolve_route_endpoints"
        if endpoint_messages:
            if _tool_failed(endpoint_messages[-1]):
                return await handler(request)
            tool_name = "recommend_travel_plan"

        scoped_tools = [
            tool
            for tool in request.tools
            if getattr(tool, "name", None) == tool_name
            or (isinstance(tool, dict) and tool.get("name") == tool_name)
        ]
        routed_request = request.override(
            tools=scoped_tools or request.tools,
            tool_choice=tool_name,
        )
        return await handler(routed_request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        """Repair deadline semantics before the composite tool is executed."""
        if request.tool_call.get("name") != "recommend_travel_plan":
            return await handler(request)
        state = request.state
        messages = state.get("messages", []) if isinstance(state, dict) else []
        content = next(
            (
                message.content
                for message in reversed(messages)
                if isinstance(message, HumanMessage)
                and isinstance(message.content, str)
            ),
            None,
        )
        if not isinstance(content, str) or not _ARRIVAL_DEADLINE_PATTERN.search(
            content
        ):
            return await handler(request)

        tool_call = request.tool_call
        raw_args = tool_call.get("args", {})
        args = dict(raw_args) if isinstance(raw_args, dict) else {}
        args["departure_time_text"] = None
        args["arrival_time_text"] = content
        args.setdefault("arrival_buffer_minutes", 15)
        return await handler(
            request.override(tool_call={**tool_call, "args": args})
        )
