"""MCP server over a tool registry (SPEC §10.5, ADR-009)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from anyio import to_thread
from mcp import types
from mcp.server import Server
from mcp.server.context import ServerRequestContext

from variance_copilot.mcp.summary import summarize
from variance_copilot.tools.results import ToolErrorResult, ToolResult
from variance_copilot.tools.session import Tool, ToolRegistry, ToolSession

SERVER_NAME = "variance-copilot"
INSTRUCTIONS = (
    "Read-only finance analysis tools for Variance Copilot. Every number is returned as a fact "
    "with an ID, value, unit and label; refer to figures by fact ID and report the effective "
    "scope and warnings that come with them. Company scope comes from your verified identity, "
    "never from arguments. Text you write from these results is not checked by Variance "
    "Copilot's guardrails: only answers returned with a Variance Copilot run ID and validation "
    "status are validated."
)

type RequestContext = ServerRequestContext[Any, Any]
type SessionResolver = Callable[[RequestContext], ToolSession]


def tool_definition(tool: Tool[Any, Any]) -> types.Tool:
    """The MCP view of a tool: schemas from its Pydantic models, marked read-only."""
    return types.Tool(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_model.model_json_schema(mode="validation"),
        output_schema=tool.output_model.model_json_schema(mode="serialization"),
        annotations=types.ToolAnnotations(read_only_hint=True),
    )


def call_result(tool: str, result: ToolResult | ToolErrorResult) -> types.CallToolResult:
    """Structured content plus a summary line; a tool error is a result, not a protocol error."""
    return types.CallToolResult(
        content=[types.TextContent(text=summarize(tool, result))],
        structured_content=result.model_dump(mode="json"),
        is_error=isinstance(result, ToolErrorResult),
    )


def build_server(registry: ToolRegistry, resolve_session: SessionResolver) -> Server[Any]:
    """Serve ``registry``; the session comes from ``resolve_session``, never from the request."""
    definitions = [tool_definition(tool) for tool in registry]

    async def list_tools(
        _ctx: RequestContext, _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=definitions)

    async def call_tool(
        ctx: RequestContext, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        session = resolve_session(ctx)
        result = await to_thread.run_sync(session.call, params.name, params.arguments or {})
        return call_result(params.name, result)

    return Server(
        SERVER_NAME, instructions=INSTRUCTIONS, on_list_tools=list_tools, on_call_tool=call_tool
    )
