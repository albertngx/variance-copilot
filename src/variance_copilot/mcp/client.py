"""The orchestrator's in-process MCP client, bound to one run's tool session (ADR-009)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp import Client
from mcp.client.client import ConnectMode

from variance_copilot.mcp.server import build_server
from variance_copilot.tools.session import ToolSession


@asynccontextmanager
async def open_tool_client(
    session: ToolSession, *, mode: ConnectMode = "auto"
) -> AsyncIterator[Client]:
    """A connected client whose every tool call runs in ``session``."""
    server = build_server(session.registry, lambda _ctx: session)
    async with Client(server, mode=mode) as client:
        yield client
