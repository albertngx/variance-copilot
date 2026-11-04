from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from decimal import Decimal

import pytest
from sqlalchemy import Connection, create_engine, text

from variance_copilot.config import Role, load_materiality
from variance_copilot.finance.codes import ErrorCode, FinanceError
from variance_copilot.tools.context import Principal, ToolContext
from variance_copilot.tools.facts import FactKind, FactRef
from variance_copilot.tools.inputs import InputModel
from variance_copilot.tools.results import ToolErrorResult, ToolResult
from variance_copilot.tools.session import Tool, ToolRegistry, ToolSession

ANALYST = Principal("analyst_es", frozenset({Role.ANALYST}), frozenset({"ES10", "DE10"}))


class EchoInput(InputModel):
    value: Decimal


class EchoResult(ToolResult):
    ref: FactRef


def echo(params: EchoInput, ctx: ToolContext) -> EchoResult:
    ref = ctx.facts.add(FactKind.AMOUNT, params.value, "USD", "echo", row_key="k", field="value")
    return EchoResult(ref=ref)


def no_forecast(params: EchoInput, ctx: ToolContext) -> EchoResult:
    raise FinanceError(ErrorCode.NO_FORECAST, "no approved forecast for 2027")


def crash_after_a_fact(params: EchoInput, ctx: ToolContext) -> EchoResult:
    echo(params, ctx)
    raise RuntimeError("connection string with a password")


def query(params: EchoInput, ctx: ToolContext) -> EchoResult:
    ctx.conn.execute(text("SELECT 1"))
    return echo(params, ctx)


def _tool(name: str, function: object) -> Tool[EchoInput, EchoResult]:
    return Tool(name, f"{name} tool", EchoInput, EchoResult, function)  # type: ignore[arg-type]


REGISTRY = ToolRegistry(
    [
        _tool("echo", echo),
        _tool("no_forecast", no_forecast),
        _tool("crash", crash_after_a_fact),
        _tool("query", query),
    ]
)


@pytest.fixture
def conn() -> Iterator[Connection]:
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        yield connection
    engine.dispose()


class Transactions:
    """Records the company scope each transaction was opened with."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn
        self.scopes: list[list[str]] = []

    @contextmanager
    def __call__(self, company_scope: Sequence[str]) -> Iterator[Connection]:
        self.scopes.append(list(company_scope))
        yield self.conn


def _session(
    conn: Connection, principal: Principal = ANALYST, timeout: float = 10
) -> tuple[ToolSession, Transactions]:
    transactions = Transactions(conn)
    session = ToolSession(REGISTRY, principal, transactions, load_materiality(), timeout=timeout)
    return session, transactions


def _error(result: ToolResult | ToolErrorResult) -> ErrorCode:
    assert isinstance(result, ToolErrorResult)
    return result.error.code


def test_calls_are_numbered_and_their_facts_enter_the_ledger(conn: Connection) -> None:
    session, _ = _session(conn)

    first = session.call("echo", {"value": "1.5"})
    second = session.call("echo", {"value": "2"})

    assert isinstance(first, EchoResult)
    assert isinstance(second, EchoResult)
    assert (first.tool_call_id, second.tool_call_id) == ("tc-1", "tc-2")
    assert (first.ref.fact_id, second.ref.fact_id) == ("F1-1", "F2-1")
    assert [f.fact_id for f in first.facts] == ["F1-1"]
    assert [f.fact_id for f in session.ledger] == ["F1-1", "F2-1"]


def test_the_transaction_scope_is_the_principals(conn: Connection) -> None:
    session, transactions = _session(conn)

    session.call("echo", {"value": "1"})

    assert transactions.scopes == [["DE10", "ES10"]]


@pytest.mark.parametrize(
    "arguments",
    [{}, {"value": "not a number"}, {"value": "1", "company_scope": ["US10"]}],
)
def test_bad_arguments_are_an_error_result(conn: Connection, arguments: dict[str, object]) -> None:
    session, transactions = _session(conn)

    result = session.call("echo", arguments)

    assert _error(result) is ErrorCode.INVALID_ARGUMENTS
    assert isinstance(result, ToolErrorResult)
    assert "not a number" not in result.error.message
    assert transactions.scopes == []


def test_unknown_tool_still_uses_a_call_number(conn: Connection) -> None:
    session, _ = _session(conn)

    result = session.call("drop_table", {})
    after = session.call("echo", {"value": "1"})

    assert _error(result) is ErrorCode.UNKNOWN_TOOL
    assert result.tool_call_id == "tc-1"
    assert after.tool_call_id == "tc-2"


def test_engine_errors_keep_their_code(conn: Connection) -> None:
    session, _ = _session(conn)

    result = session.call("no_forecast", {"value": "1"})

    assert isinstance(result, ToolErrorResult)
    assert result.error.code is ErrorCode.NO_FORECAST
    assert result.error.message == "no approved forecast for 2027"


def test_unexpected_errors_are_logged_but_not_shown(
    conn: Connection, caplog: pytest.LogCaptureFixture
) -> None:
    session, _ = _session(conn)

    result = session.call("crash", {"value": "1"})

    assert isinstance(result, ToolErrorResult)
    assert result.error.code is ErrorCode.INTERNAL_ERROR
    assert "password" not in result.error.message
    assert "tool crash failed" in caplog.text
    assert len(session.ledger) == 0


@pytest.mark.parametrize(
    "principal",
    [
        Principal("auditor", frozenset({Role.AUDITOR}), frozenset({"ES10"})),
        Principal("admin", frozenset({Role.ADMIN}), frozenset({"ES10"})),
        Principal("nobody", frozenset({Role.ANALYST}), frozenset()),
    ],
)
def test_callers_without_finance_access_are_forbidden(
    conn: Connection, principal: Principal
) -> None:
    session, transactions = _session(conn, principal)

    assert _error(session.call("echo", {"value": "1"})) is ErrorCode.FORBIDDEN
    assert transactions.scopes == []


def test_exhausted_time_budget_stops_the_next_statement(conn: Connection) -> None:
    session, _ = _session(conn, timeout=0)

    result = session.call("query", {"value": "1"})

    assert _error(result) is ErrorCode.TIMEOUT
    assert len(session.ledger) == 0


def test_registry_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="echo"):
        ToolRegistry([_tool("echo", echo), _tool("echo", echo)])


def test_registry_lists_tools_by_name() -> None:
    assert [t.name for t in REGISTRY] == ["crash", "echo", "no_forecast", "query"]
    assert REGISTRY.get("missing") is None
