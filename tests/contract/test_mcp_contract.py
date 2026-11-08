from __future__ import annotations

import difflib
import json
from pathlib import Path

import pytest
from mcp import Client

from variance_copilot.mcp.server import build_server
from variance_copilot.tools.catalog import DATA_TOOLS
from variance_copilot.tools.session import ToolSession

pytestmark = pytest.mark.anyio

SNAPSHOT = Path(__file__).parent / "snapshots" / "mcp_tools_list.json"


def _no_session(_ctx: object) -> ToolSession:
    raise AssertionError("listing tools needs no session")


async def _served_contract() -> str:
    async with Client(build_server(DATA_TOOLS, _no_session)) as client:
        listing = await client.list_tools()
        server = client.server_info
        contract = {
            "server": {
                "name": server.name if server else None,
                "instructions": client.instructions,
            },
            "tools": [
                t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in listing.tools
            ],
        }
    return json.dumps(contract, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


async def test_tools_list_matches_the_reviewed_snapshot(update_snapshots: bool) -> None:
    served = await _served_contract()
    if update_snapshots:
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(served, encoding="utf-8")

    reviewed = SNAPSHOT.read_text(encoding="utf-8") if SNAPSHOT.exists() else ""
    if served != reviewed:
        diff = difflib.unified_diff(
            reviewed.splitlines(keepends=True),
            served.splitlines(keepends=True),
            fromfile=f"{SNAPSHOT.name} (reviewed)",
            tofile="tools/list (served)",
        )
        pytest.fail(
            "The MCP tool contract changed. Review the diff, then run `make snapshots` and commit "
            f"the snapshot with the change.\n\n{''.join(diff)}",
            pytrace=False,
        )
