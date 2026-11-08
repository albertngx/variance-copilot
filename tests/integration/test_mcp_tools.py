from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Connection

from variance_copilot.config import Role
from variance_copilot.mcp.client import open_tool_client
from variance_copilot.tools.context import Principal
from variance_copilot.tools.session import ToolSession

pytestmark = pytest.mark.anyio

ANALYST_US = Principal("analyst_us", frozenset({Role.ANALYST}), frozenset({"US10"}))
YTD_P09 = {"fiscal_year": 2026, "period": 9, "view": "YTD"}
CALLS: dict[str, dict[str, Any]] = {
    "compare": {"scope": {"company_codes": ["US10"]}, "period": YTD_P09, "comparison": "BUD26"},
    "get_fx_rates": {
        "currencies": ["CHF"],
        "rate_types": ["AVG", "B26"],
        "period": {"fiscal_year": 2026, "period": 3, "view": "QTD"},
    },
    "get_period_status": {"fiscal_year": 2026},
    "get_pnl": {"scope": {}, "period": YTD_P09},
    "journal_lines": {"scope": {}, "period": YTD_P09, "limit": 5},
    "list_dimension_members": {"dimension": "company_code"},
}

type Sessions = Callable[[Connection, Principal], ToolSession]


async def test_compare_through_mcp_matches_a_direct_call(
    ci_ledger: Connection, tool_session: Sessions
) -> None:
    direct = tool_session(ci_ledger, ANALYST_US).call("compare", CALLS["compare"])

    async with open_tool_client(tool_session(ci_ledger, ANALYST_US), mode="legacy") as client:
        result = await client.call_tool("compare", CALLS["compare"])

    assert not result.is_error
    assert result.structured_content == direct.model_dump(mode="json")
    assert result.structured_content["rows"]


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_every_data_tool_returns_what_its_output_schema_declares(
    ci_ledger: Connection, tool_session: Sessions, mode: str
) -> None:
    """The client validates each structured result against the listed output schema."""
    session = tool_session(ci_ledger, ANALYST_US)

    async with open_tool_client(session, mode=mode) as client:
        listed = {tool.name for tool in (await client.list_tools()).tools}
        results = {name: await client.call_tool(name, CALLS[name]) for name in sorted(listed)}

    assert listed == set(CALLS)
    assert not [name for name, result in results.items() if result.is_error]
    assert {fact.provenance.tool for fact in session.ledger} == {
        "compare",
        "get_fx_rates",
        "get_pnl",
        "journal_lines",
    }
