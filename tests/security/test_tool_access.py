from __future__ import annotations

from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Connection, text

from variance_copilot.config import Role, load_materiality
from variance_copilot.finance.codes import ErrorCode
from variance_copilot.tools.catalog import DATA_TOOLS
from variance_copilot.tools.context import Principal, ToolContext, TransactionFactory
from variance_copilot.tools.inputs import InputModel
from variance_copilot.tools.results import ToolErrorResult, ToolResult
from variance_copilot.tools.session import Tool, ToolRegistry, ToolSession

ANALYST_ES = Principal("analyst_es", frozenset({Role.ANALYST}), frozenset({"ES10", "DE10"}))
GROUP_FPA = Principal(
    "group_fpa", frozenset({Role.GROUP_ANALYST}), frozenset({"CH10", "ES10", "IE10", "US10"})
)
VISIBLE = {"ES10", "DE10"}
YTD_P09 = {"fiscal_year": 2026, "period": 9, "view": "YTD"}
PAYLOADS = [
    "'; RESET ROLE; --",
    "x' OR company_code = 'US10' --",
    "'); SELECT set_config('app.company_scope', 'US10', false); --",
    "%%",
    "__",
]


class NoInput(InputModel):
    pass


class Identity(ToolResult):
    user: str
    company_scope: str


def whoami(params: NoInput, ctx: ToolContext) -> Identity:
    user, scope = ctx.conn.execute(
        text("SELECT current_user, current_setting('app.company_scope')")
    ).one()
    return Identity(user=user, company_scope=scope)


REGISTRY = ToolRegistry(
    [*DATA_TOOLS, Tool("whoami", "Database identity", NoInput, Identity, whoami)]
)


@pytest.fixture
def transactions(
    ci_ledger: Connection, reader_transactions: Callable[[Connection], TransactionFactory]
) -> TransactionFactory:
    return reader_transactions(ci_ledger)


@pytest.fixture
def es_session(transactions: TransactionFactory) -> ToolSession:
    return ToolSession(REGISTRY, ANALYST_ES, transactions, load_materiality())


def _ok(session: ToolSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = session.call(name, arguments)
    assert not isinstance(result, ToolErrorResult), result
    return result.model_dump(mode="json")


def _error(session: ToolSession, name: str, arguments: dict[str, Any]) -> ErrorCode:
    result = session.call(name, arguments)
    assert isinstance(result, ToolErrorResult), result
    return result.error.code


def _company_codes(node: Any) -> Iterator[str]:
    """Every company code a result mentions as the owner of a row."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "company_code" and isinstance(value, str):
                yield value
            elif key == "company_codes":
                yield from value
            else:
                yield from _company_codes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _company_codes(item)


def _value(result: dict[str, Any], ref: dict[str, str]) -> Decimal:
    return Decimal(next(f["value"] for f in result["facts"] if f["fact_id"] == ref["fact_id"]))


CALLS = [
    ("get_pnl", {"scope": {}, "period": YTD_P09, "group_by": ["company_code", "pnl_line"]}),
    (
        "compare",
        {"scope": {}, "period": YTD_P09, "comparison": "BUD26", "group_by": ["company_code"]},
    ),
    ("journal_lines", {"scope": {}, "period": YTD_P09, "limit": 200}),
    ("get_period_status", {"fiscal_year": 2026}),
    ("list_dimension_members", {"dimension": "cost_center", "limit": 200}),
    ("list_dimension_members", {"dimension": "wbs_element", "limit": 200}),
]


@pytest.mark.parametrize(("name", "arguments"), CALLS, ids=[c[0] for c in CALLS])
def test_es_analyst_sees_only_own_companies_through_every_tool(
    es_session: ToolSession, name: str, arguments: dict[str, Any]
) -> None:
    result = _ok(es_session, name, arguments)

    assert set(_company_codes(result)) <= VISIBLE


def test_es_analyst_totals_are_es10_only(es_session: ToolSession, ci_ledger: Connection) -> None:
    result = _ok(es_session, "get_pnl", {"scope": {}, "period": YTD_P09})
    companies = _ok(es_session, "list_dimension_members", {"dimension": "company_code"})

    es10 = ci_ledger.execute(
        text(
            "SELECT SUM(amount_gc) FROM rpt.pnl_actual "
            "WHERE company_code = 'ES10' AND fiscal_year = 2026 AND period <= 9"
        )
    ).scalar_one()
    assert _value(result, result["total"]["amount"]) == es10
    assert {m["code"] for m in companies["members"]} <= VISIBLE


@pytest.mark.parametrize(
    ("name", "arguments", "code"),
    [
        ("get_pnl", {"scope": {"company_codes": ["US10"]}, "period": YTD_P09}, "NO_ACCESS"),
        (
            "compare",
            {"scope": {"company_codes": ["US10"]}, "period": YTD_P09, "comparison": "BUD26"},
            "NO_ACCESS",
        ),
        (
            "journal_lines",
            {"scope": {"company_codes": ["US10"]}, "period": YTD_P09},
            "NO_ACCESS",
        ),
        ("get_period_status", {"company_codes": ["US10"], "fiscal_year": 2026}, "NO_ACCESS"),
        ("get_pnl", {"scope": {"level": "GROUP"}, "period": YTD_P09}, "FORBIDDEN"),
    ],
)
def test_requests_for_other_companies_are_refused(
    es_session: ToolSession, name: str, arguments: dict[str, Any], code: str
) -> None:
    assert _error(es_session, name, arguments) is ErrorCode(code)


def test_filters_cannot_reach_us10_lines(es_session: ToolSession, ci_ledger: Connection) -> None:
    document, account, assignment = ci_ledger.execute(
        text(
            "SELECT document_number, gl_account, assignment FROM rpt.pnl_actual "
            "WHERE company_code = 'US10' AND assignment LIKE 'TRUEUP-%' LIMIT 1"
        )
    ).one()
    base = {"scope": {}, "period": YTD_P09, "limit": 200}

    by_document = _ok(es_session, "journal_lines", {**base, "document_number": document})
    by_account = _ok(es_session, "journal_lines", {**base, "gl_account": account})
    by_text = _ok(es_session, "journal_lines", {**base, "text": assignment})

    for result in (by_document, by_account, by_text):
        assert {line["company_code"] for line in result["lines"]} <= VISIBLE
    assert by_text["lines"] == []


@pytest.mark.parametrize("payload", PAYLOADS)
def test_sql_in_free_text_is_only_text(es_session: ToolSession, payload: str) -> None:
    lines = _ok(es_session, "journal_lines", {"scope": {}, "period": YTD_P09, "text": payload})
    members = _ok(
        es_session, "list_dimension_members", {"dimension": "company_code", "filter": payload}
    )

    assert set(_company_codes(lines)) <= VISIBLE
    assert lines["lines"] == []
    assert members["members"] == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"scope": {"company_codes": ["US10' OR '1'='1"]}},
        {"scope": {"brand": "CARDIVEX' OR '1'='1"}},
        {"scope": {"cost_center": "'; RESET ROLE; --"}},
        {"scope": {"profit_center": "x) OR (1=1"}},
        {"gl_account": "40000000 OR 1=1"},
        {"document_number": "0090000001'--"},
        {"document_type": "RV' OR 'a'='a"},
        {"vendor": "V1000; DROP TABLE fin.journal_line"},
        {"material": "set_config('app.company_scope','US10',false)"},
        {"company_scope": ["US10"]},
        {"_meta": {"company_scope": ["US10"]}},
        {"run_id": "00000000-0000-0000-0000-000000000000"},
        {"roles": ["group_analyst"]},
    ],
)
def test_sql_or_scope_in_structured_fields_is_rejected(
    es_session: ToolSession, arguments: dict[str, Any]
) -> None:
    base: dict[str, Any] = {"scope": {}, "period": YTD_P09}

    assert _error(es_session, "journal_lines", {**base, **arguments}) is ErrorCode.INVALID_ARGUMENTS


def test_tools_run_as_the_reader_with_the_principals_scope(es_session: ToolSession) -> None:
    for payload in PAYLOADS:
        es_session.call("journal_lines", {"scope": {}, "period": YTD_P09, "text": payload})

    identity = _ok(es_session, "whoami", {})

    assert identity["user"] == "vc_tool_reader"
    assert identity["company_scope"] == "DE10,ES10"


def test_group_totals_exclude_every_trading_partner_line(
    transactions: TransactionFactory, ci_ledger: Connection
) -> None:
    session = ToolSession(REGISTRY, GROUP_FPA, transactions, load_materiality())
    by_line = {"period": YTD_P09, "group_by": ["pnl_line"]}

    group = _ok(session, "get_pnl", {**by_line, "scope": {"level": "GROUP"}})
    entities = _ok(session, "get_pnl", {**by_line, "scope": {}})
    lines = _ok(session, "journal_lines", {"scope": {"level": "GROUP"}, "period": YTD_P09})

    sums = dict(
        ci_ledger.execute(
            text(
                "SELECT pnl_line || '|' || is_intercompany, SUM(amount_gc) FROM rpt.pnl_actual "
                "WHERE fiscal_year = 2026 AND period <= 9 GROUP BY 1"
            )
        ).all()
    )
    for row in group["rows"]:
        line = row["key"]["pnl_line"]
        assert _value(group, row["amount"]) == sums[f"{line}|false"]
    revenue = next(r for r in entities["rows"] if r["key"]["pnl_line"] == "REVENUE")
    assert _value(entities, revenue["amount"]) == sums["REVENUE|false"] + sums["REVENUE|true"]
    assert sums["REVENUE|true"] != 0
    assert all(line["trading_partner"] is None for line in lines["lines"])
