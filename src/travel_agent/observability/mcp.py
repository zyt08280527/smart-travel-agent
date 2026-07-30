"""Carry hidden observability metadata across MCP and LangChain."""

from collections.abc import Awaitable, Callable

from langchain_mcp_adapters.interceptors import (
    MCPToolCallRequest,
    MCPToolCallResult,
)
from mcp.types import CallToolResult, TextContent

from travel_agent.observability.http import ExternalHttpRequest


def observed_text_result(
    text: str,
    requests: list[ExternalHttpRequest],
) -> CallToolResult:
    """Return model-visible text plus client-only HTTP observations."""
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        _meta={
            "observability": {
                "external_http_request_count": len(requests),
                "external_http_requests": [
                    request.model_dump(mode="json") for request in requests
                ],
            }
        },
    )


async def preserve_observability_metadata(
    request: MCPToolCallRequest,
    handler: Callable[
        [MCPToolCallRequest],
        Awaitable[MCPToolCallResult],
    ],
) -> MCPToolCallResult:
    """Preserve MCP _meta in ToolMessage.artifact before adapter conversion."""
    result = await handler(request)
    if not isinstance(result, CallToolResult):
        return result
    observability = (result.meta or {}).get("observability")
    if not isinstance(observability, dict):
        return result
    structured_content = dict(result.structuredContent or {})
    structured_content["observability"] = observability
    return result.model_copy(
        update={"structuredContent": structured_content}
    )
