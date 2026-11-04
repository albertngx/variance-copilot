from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import DBAPIError

from variance_copilot.config import Role
from variance_copilot.db import sqlalchemy_url
from variance_copilot.finance.codes import ErrorCode, WarningCode
from variance_copilot.tools.context import Principal, deadline
from variance_copilot.tools.results import ToolErrorResult
from variance_copilot.tools.session import ToolSession

ANALYST_US = Principal("analyst_us", frozenset({Role.ANALYST}), frozenset({"US10"}))
ANALYST_CH = Principal("analyst_ch", frozenset({Role.ANALYST}), frozenset({"CH10"}))
ANALYST_EU = Principal("analyst_eu", frozenset({Role.ANALYST}), frozenset({"CH10", "ES10"}))
YTD_P09 = {"fiscal_year": 2026, "period": 9, "view": "YTD"}

type Sessions = Callable[[Principal], ToolSession]


@pytest.fixture
def sessions(
    ci_ledger: Connection, tool_session: Callable[[Connection, Principal], ToolSession]
) -> Sessions:
    return lambda principal: tool_session(ci_ledger, principal)


def _ok(session: ToolSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = session.call(name, arguments)
    assert not isinstance(result, ToolErrorResult), result
    return result.model_dump(mode="json")


def _error(session: ToolSession, name: str, arguments: dict[str, Any]) -> ErrorCode:
    result = session.call(name, arguments)
    assert isinstance(result, ToolErrorResult), result
    return result.error.code


def _facts(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["fact_id"]: f for f in result["facts"]}


def _value(result: dict[str, Any], ref: dict[str, str] | None) -> Decimal:
    assert ref is not None
    return Decimal(_facts(result)[ref["fact_id"]]["value"])


def _sql_sum(conn: Connection, view: str, where: str, **params: Any) -> Decimal:
    total = conn.execute(
        text(f"SELECT COALESCE(SUM(amount_gc), 0) FROM rpt.{view} WHERE {where}"), params
    ).scalar_one()
    return Decimal(total)


def test_compare_matches_the_contract_shape(sessions: Sessions, ci_ledger: Connection) -> None:
    result = _ok(
        sessions(ANALYST_US),
        "compare",
        {"scope": {"company_codes": ["US10"]}, "period": YTD_P09, "comparison": "BUD26"},
    )

    assert set(result) == {"tool_call_id", "warnings", "facts", "effective_scope", "rows", "total"}
    assert result["tool_call_id"] == "tc-1"
    assert result["warnings"] == []
    scope = result["effective_scope"]
    assert scope["company_codes"] == ["US10"]
    assert scope["periods"] == ["2026-01", "2026-09"]
    assert (scope["actual"], scope["comparison"]) == ("ACT", "BUD26")
    assert (scope["currency_basis"], scope["basis"]) == ("GC", "IFRS")

    facts = _facts(result)
    revenue = next(r for r in result["rows"] if r["key"] == {"pnl_line": "REVENUE"})
    for name in ("actual", "comparison", "delta", "delta_pct", "fx_effect", "delta_operational"):
        assert revenue[name]["fact_id"] in facts
    delta = facts[revenue["delta"]["fact_id"]]
    assert (delta["favorable"], delta["magnitude"]) == (revenue["favorable"], revenue["magnitude"])
    assert facts[revenue["delta_pct"]["fact_id"]]["unit"] == "%"
    assert delta["provenance"]["row_key"] == "pnl_line=REVENUE"

    where = "company_code = 'US10' AND fiscal_year = 2026 AND period <= 9 AND pnl_line = 'REVENUE'"
    actual = _sql_sum(ci_ledger, "pnl_actual", where)
    budget = _sql_sum(ci_ledger, "pnl_plan", f"{where} AND plan_category = 'BUD26'")
    assert _value(result, revenue["actual"]) == actual
    assert _value(result, revenue["comparison"]) == budget
    assert _value(result, revenue["delta"]) == actual - budget
    assert _value(result, revenue["fx_effect"]) == 0
    assert revenue["material"] is True


def test_compare_rows_are_ordered_and_repeatable(sessions: Sessions) -> None:
    arguments = {
        "scope": {"company_codes": ["US10"]},
        "period": YTD_P09,
        "comparison": "LATEST_FC",
        "group_by": ["brand", "pnl_line"],
    }

    first = _ok(sessions(ANALYST_US), "compare", arguments)
    second = _ok(sessions(ANALYST_US), "compare", arguments)

    assert first == second
    deltas = [abs(_value(first, row["delta"])) for row in first["rows"]]
    assert deltas == sorted(deltas, reverse=True)
    assert first["effective_scope"]["comparison"] == "FC226"


def test_fx_and_operational_variance_add_up_on_every_row(sessions: Sessions) -> None:
    base = {"scope": {"company_codes": ["CH10"]}, "period": YTD_P09, "comparison": "BUD26"}
    session = sessions(ANALYST_CH)

    gc = _ok(session, "compare", base)
    cc = _ok(session, "compare", {**base, "currency_basis": "CC"})

    assert any(_value(gc, row["fx_effect"]) != 0 for row in gc["rows"])
    for row in [*gc["rows"], gc["total"]]:
        operational, fx = _value(gc, row["delta_operational"]), _value(gc, row["fx_effect"])
        assert _value(gc, row["delta"]) == operational + fx
    operational_by_key = {
        str(row["key"]): _value(gc, row["delta_operational"]) for row in gc["rows"]
    }
    for row in cc["rows"]:
        assert _value(cc, row["delta"]) == operational_by_key[str(row["key"])]


def test_local_currency_needs_a_single_currency(sessions: Sessions) -> None:
    arguments = {"period": YTD_P09, "comparison": "BUD26", "currency_basis": "LC"}
    session = sessions(ANALYST_EU)

    chf = _ok(session, "compare", {**arguments, "scope": {"company_codes": ["CH10"]}})
    mixed = _error(session, "compare", {**arguments, "scope": {}})

    assert {f["unit"] for f in chf["facts"]} <= {"CHF", "%"}
    assert all(row["fx_effect"] is None for row in chf["rows"])
    assert mixed is ErrorCode.MIXED_CURRENCIES


def test_prior_year_comparison_echoes_its_periods(sessions: Sessions) -> None:
    result = _ok(
        sessions(ANALYST_US),
        "compare",
        {"scope": {}, "period": YTD_P09, "comparison": "PY"},
    )

    assert result["effective_scope"]["comparison_periods"] == ["2025-01", "2025-09"]
    assert WarningCode.NO_COMPARISON_BASE in result["warnings"]
    assert all(row["delta_pct"] is None for row in result["rows"])


def test_get_pnl_total_and_core_basis(sessions: Sessions, ci_ledger: Connection) -> None:
    arguments = {"scope": {"company_codes": ["CH10"]}, "period": YTD_P09}
    session = sessions(ANALYST_CH)

    ifrs = _ok(session, "get_pnl", arguments)
    core = _ok(session, "get_pnl", {**arguments, "basis": "CORE"})

    where = "company_code = 'CH10' AND fiscal_year = 2026 AND period <= 9"
    adjustments = _sql_sum(ci_ledger, "pnl_actual", f"{where} AND core_adjustment IS NOT NULL")
    assert adjustments != 0
    assert _value(ifrs, ifrs["total"]["amount"]) == _sql_sum(ci_ledger, "pnl_actual", where)
    assert _value(ifrs, ifrs["total"]["amount"]) - _value(core, core["total"]["amount"]) == (
        adjustments
    )
    assert ifrs["effective_scope"]["version"] == "ACT"


def test_group_by_period_keeps_months_apart(sessions: Sessions) -> None:
    result = _ok(
        sessions(ANALYST_US),
        "compare",
        {
            "scope": {},
            "period": {"fiscal_year": 2026, "period": 3, "view": "QTD"},
            "comparison": "BUD26",
            "group_by": ["period"],
        },
    )

    assert sorted(row["key"]["period"] for row in result["rows"]) == [
        "2026-01",
        "2026-02",
        "2026-03",
    ]


def test_journal_lines_cap_at_200_and_flag_truncation(sessions: Sessions) -> None:
    result = _ok(
        sessions(ANALYST_US), "journal_lines", {"scope": {}, "period": YTD_P09, "limit": 200}
    )

    assert len(result["lines"]) == 200
    assert WarningCode.TRUNCATED in result["warnings"]
    amounts = [abs(_value(result, line["amount_gc"])) for line in result["lines"]]
    assert amounts == sorted(amounts, reverse=True)
    assert len(result["facts"]) == 400


def test_journal_line_filters(sessions: Sessions) -> None:
    session = sessions(ANALYST_US)
    base = {"scope": {}, "period": YTD_P09}

    trueups = _ok(session, "journal_lines", {**base, "text": "TRUEUP"})
    large = _ok(session, "journal_lines", {**base, "min_amount": "5000000", "limit": 20})
    account = _ok(session, "journal_lines", {**base, "gl_account": "40000000", "limit": 5})

    assert trueups["lines"]
    assert all(line["assignment"].startswith("TRUEUP-") for line in trueups["lines"])
    assert all(abs(_value(large, line["amount_gc"])) >= 5_000_000 for line in large["lines"])
    assert {line["gl_account"] for line in account["lines"]} == {"40000000"}


def test_partial_scope_and_no_data(sessions: Sessions) -> None:
    session = sessions(ANALYST_US)

    partial = _ok(
        session,
        "get_pnl",
        {"scope": {"company_codes": ["US10", "CH10"]}, "period": YTD_P09},
    )
    empty = _ok(
        session,
        "get_pnl",
        {"scope": {}, "period": {"fiscal_year": 2026, "period": 11, "view": "MTD"}},
    )

    assert partial["warnings"] == [WarningCode.PARTIAL_SCOPE]
    assert partial["effective_scope"]["company_codes"] == ["US10"]
    assert empty["warnings"] == [WarningCode.NO_DATA]
    assert empty["rows"] == []
    assert empty["total"] is None


def test_open_period_applies_to_actuals_only(sessions: Sessions) -> None:
    session = sessions(ANALYST_US)
    ytd_p10 = {"fiscal_year": 2026, "period": 10, "view": "YTD"}

    actual = _ok(session, "compare", {"scope": {}, "period": ytd_p10, "comparison": "BUD26"})
    budget = _ok(session, "get_pnl", {"scope": {}, "period": ytd_p10, "version": "BUD26"})

    assert WarningCode.OPEN_PERIOD in actual["warnings"]
    assert WarningCode.OPEN_PERIOD not in budget["warnings"]


def test_fx_rates_come_from_the_rate_table(sessions: Sessions, ci_ledger: Connection) -> None:
    result = _ok(
        sessions(ANALYST_US),
        "get_fx_rates",
        {
            "currencies": ["CHF"],
            "rate_types": ["AVG", "B26"],
            "period": {"fiscal_year": 2026, "period": 3, "view": "QTD"},
        },
    )

    stored = dict(
        ci_ledger.execute(
            text(
                "SELECT rate_type || '|' || to_char(valid_from, 'YYYY-MM'), rate FROM md.fx_rate "
                "WHERE from_currency = 'CHF' AND rate_type IN ('AVG', 'B26')"
            )
        ).all()
    )
    assert len(result["rows"]) == 6
    for row in result["rows"]:
        month = row["period"] if row["rate_type"] == "AVG" else "2026-01"
        assert _value(result, row["rate"]) == stored[f"{row['rate_type']}|{month}"]
    assert {f["unit"] for f in result["facts"]} == {"USD/CHF"}


def test_period_status_for_visible_companies(sessions: Sessions) -> None:
    result = _ok(sessions(ANALYST_US), "get_period_status", {"fiscal_year": 2026})

    statuses = {row["period"]: row["status"] for row in result["rows"]}
    assert {row["company_code"] for row in result["rows"]} == {"US10"}
    assert statuses["2026-09"] == "CLOSED"
    assert statuses["2026-10"] == "OPEN"
    assert result["facts"] == []


def test_dimension_members_follow_company_scope(sessions: Sessions) -> None:
    session = sessions(ANALYST_US)

    cost_centers = _ok(
        session, "list_dimension_members", {"dimension": "cost_center", "limit": 200}
    )
    brands = _ok(session, "list_dimension_members", {"dimension": "brand", "filter": "cardi"})
    companies = _ok(session, "list_dimension_members", {"dimension": "company_code"})

    assert cost_centers["members"]
    assert {m["attributes"]["company_code"] for m in cost_centers["members"]} == {"US10"}
    assert [m["code"] for m in brands["members"]] == ["CARDIVEX"]
    assert [m["code"] for m in companies["members"]] == ["US10"]


def test_deadline_cancels_a_long_statement(migrated_url: str) -> None:
    engine = create_engine(sqlalchemy_url(migrated_url))
    try:
        with (
            engine.connect() as conn,
            pytest.raises(DBAPIError) as excinfo,
            deadline(conn, 0.3),
        ):
            conn.execute(text("SELECT pg_sleep(2)"))
    finally:
        engine.dispose()

    assert type(excinfo.value.orig).__name__ == "QueryCanceled"
