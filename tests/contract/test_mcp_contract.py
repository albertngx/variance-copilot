from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from mcp import Client

from variance_copilot.mcp.server import build_server
from variance_copilot.tools.catalog import DATA_TOOLS
from variance_copilot.tools.session import ToolSession

pytestmark = pytest.mark.anyio


def _no_session(_ctx: object) -> ToolSession:
    raise AssertionError("listing tools needs no session")


async def _served_contract() -> dict[str, Any]:
    async with Client(build_server(DATA_TOOLS, _no_session)) as client:
        listing = await client.list_tools()
        server = client.server_info
        return {
            "server": {
                "name": server.name if server else None,
                "instructions": client.instructions,
            },
            "tools": [
                t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in listing.tools
            ],
        }


async def test_tools_list_matches_the_reviewed_snapshot(
    snapshot: Callable[[str, Any], None],
) -> None:
    snapshot("mcp_tools_list", await _served_contract())
