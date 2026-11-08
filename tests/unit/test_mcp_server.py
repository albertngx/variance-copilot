from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from decimal import Decimal
from typing import Any
from uuid import uuid4

import anyio
import pytest
from mcp import Client
from mcp.types import CallToolResult
from sqlalchemy import Connection, create_engine

from variance_copilot.config import Role, load_materiality
from variance_copilot.finance.codes import ErrorCode, FinanceError
from variance_copilot.mcp.client import open_tool_client
from variance_copilot.mcp.server import INSTRUCTIONS, build_server
from variance_copilot.tools.catalog import DATA_TOOLS
from variance_copilot.tools.context import Principal, ToolContext
from variance_copilot.tools.facts import FactKind, FactRef
from variance_copilot.tools.inputs import InputModel
from variance_copilot.tools.results import ToolResult
from variance_copilot.tools.session import Tool, ToolRegistry, ToolSession

pytestmark = pytest.mark.anyio

ANALYST = Principal("analyst_es", frozenset({Role.ANALYST}), frozenset({"ES10", "DE10"}))


class EchoInput(InputModel):
    value: Decimal


class EchoResult(ToolResult):
    rows: list[FactRef]


def echo(params: EchoInput, ctx: ToolContext) -> EchoResult:
    ref = ctx.facts.add(FactKind.AMOUNT, params.value, "USD", "echo", row_key="k", field="value")
    return EchoResult(rows=[ref])


def no_forecast(params: EchoInput, ctx: ToolContext) -> EchoResult:
    raise FinanceError(ErrorCode.NO_FORECAST, "no approved forecast for 2027")


REGISTRY = ToolRegistry(
    [
        Tool("echo", "Echo a value as a fact.", EchoInput, EchoResult, echo),
        Tool("no_forecast", "Always fails.", EchoInput, EchoResult, no_forecast),
    ]
)


class Transactions:
    """Records the company scope each transaction was opened with."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn
        self.scopes: list[list[str]] = []

    @contextmanager
    def __call__(self, company_scope: Sequence[str]) -> Iterator[Connection]:
        self.scopes.append(list(company_scope))
        yield self.conn


@pytest.fixture
def transactions() -> Iterator[Transactions]:
    engine = create_engine("sqlite://")
    with engine.connect() as conn:
        yield Transactions(conn)
    engine.dispose()


@pytest.fixture(params=["auto", "legacy"])
def mode(request: pytest.FixtureRequest) -> str:
    """Direct in-process dispatch, and JSON-RPC over memory streams as remote clients see it."""
    return str(request.param)


def _session(transactions: Transactions, registry: ToolRegistry = REGISTRY) -> ToolSession:
    return ToolSession(registry, ANALYST, transactions, load_materiality())


def _no_session(_ctx: object) -> ToolSession:
    raise AssertionError("listing tools needs no session")


def _structured(result: CallToolResult) -> dict[str, Any]:
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def _text(result: CallToolResult) -> str:
    [block] = result.content
    assert block.type == "text"
    return block.text


async def test_tools_list_shows_every_registry_tool_with_its_schemas(mode: str) -> None:
    async with Client(build_server(DATA_TOOLS, _no_session), mode=mode) as client:
        listing = await client.list_tools()
        instructions = client.instructions

    assert [t.name for t in listing.tools] == [t.name for t in DATA_TOOLS]
    for listed, tool in zip(listing.tools, DATA_TOOLS, strict=True):
        assert listed.description == tool.description
        assert listed.input_schema == tool.input_model.model_json_schema(mode="validation")
        assert listed.output_schema == tool.output_model.model_json_schema(mode="serialization")
        assert listed.annotations is not None
        assert listed.annotations.read_only_hint is True
    assert instructions == INSTRUCTIONS


async def test_a_call_returns_structured_content_and_a_summary_without_figures(
    transactions: Transactions, mode: str
) -> None:
    session = _session(transactions)

    async with open_tool_client(session, mode=mode) as client:
        result = await client.call_tool("echo", {"value": "1234.5"})

    content = _structured(result)
    assert not result.is_error
    assert content["tool_call_id"] == "tc-1"
    assert content["rows"] == [{"fact_id": "F1-1"}]
    assert content["facts"][0]["value"] == "1234.5"
    assert session.ledger.get("F1-1") is not None
    assert _text(result).startswith("echo tc-1: rows=1, facts=1")
    assert "1234" not in _text(result)


@pytest.mark.parametrize(
    ("tool", "arguments", "code"),
    [
        ("no_forecast", {"value": "1"}, ErrorCode.NO_FORECAST),
        ("missing", {}, ErrorCode.UNKNOWN_TOOL),
        ("echo", {"value": "not a number"}, ErrorCode.INVALID_ARGUMENTS),
    ],
)
async def test_tool_errors_are_results_not_protocol_errors(
    transactions: Transactions, mode: str, tool: str, arguments: dict[str, Any], code: ErrorCode
) -> None:
    async with open_tool_client(_session(transactions), mode=mode) as client:
        result = await client.call_tool(tool, arguments)

    assert result.is_error
    assert _structured(result) == {
        "tool_call_id": "tc-1",
        "error": {"code": code, "message": _structured(result)["error"]["message"]},
    }
    assert _text(result).startswith(f"{tool} tc-1 failed: {code}")


async def test_scope_and_run_ids_in_meta_are_ignored(transactions: Transactions, mode: str) -> None:
    session = _session(transactions)
    run_id = session.run_id
    meta = {
        "company_scope": ["US10"],
        "roles": ["group_analyst"],
        "run_id": str(uuid4()),
        "tool_call_id": "tc-99",
    }

    async with open_tool_client(session, mode=mode) as client:
        result = await client.call_tool("echo", {"value": "1"}, meta=meta)

    content = _structured(result)
    assert not result.is_error
    assert transactions.scopes == [["DE10", "ES10"]]
    assert content["tool_call_id"] == "tc-1"
    assert content["facts"][0]["provenance"]["tool_call_id"] == "tc-1"
    assert (session.principal, session.run_id) == (ANALYST, run_id)


@pytest.mark.parametrize("field", ["company_scope", "roles", "run_id", "tool_call_id"])
async def test_scope_and_run_ids_in_arguments_are_rejected(
    transactions: Transactions, field: str
) -> None:
    async with open_tool_client(_session(transactions)) as client:
        result = await client.call_tool("echo", {"value": "1", field: ["US10"]})

    assert result.is_error
    assert _structured(result)["error"]["code"] == ErrorCode.INVALID_ARGUMENTS
    assert transactions.scopes == []


async def test_concurrent_calls_run_one_at_a_time(transactions: Transactions, mode: str) -> None:
    running: list[str] = []
    overlap: list[int] = []

    def slow(params: EchoInput, ctx: ToolContext) -> EchoResult:
        running.append(ctx.tool_call_id)
        overlap.append(len(running))
        time.sleep(0.01)
        running.remove(ctx.tool_call_id)
        return echo(params, ctx)

    session = _session(
        transactions, ToolRegistry([Tool("slow", "Slow.", EchoInput, EchoResult, slow)])
    )
    results: list[CallToolResult] = []

    async with open_tool_client(session, mode=mode) as client:

        async def call(value: int) -> None:
            results.append(await client.call_tool("slow", {"value": str(value)}))

        async with anyio.create_task_group() as group:
            for value in range(8):
                group.start_soon(call, value)

    assert max(overlap) == 1
    assert sorted(_structured(r)["tool_call_id"] for r in results) == sorted(
        f"tc-{n}" for n in range(1, 9)
    )
    assert len(session.ledger) == 8
