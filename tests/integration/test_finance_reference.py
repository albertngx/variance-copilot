from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from sqlalchemy import Connection, text

from variance_copilot.finance.codes import WarningCode
from variance_copilot.finance.periods import (
    Period,
    PeriodView,
    ViewSelection,
    period_warnings,
    resolve_periods,
)
from variance_copilot.finance.reference import (
    load_fx_rates,
    load_period_statuses,
    load_plan_versions,
)
from variance_copilot.finance.translation import LocalAmount, fx_split
from variance_copilot.finance.versions import resolve_version
from variance_copilot.tools.context import TransactionFactory

YTD_P09 = resolve_periods(ViewSelection(2026, 9, PeriodView.YTD))


@pytest.fixture
def tool_reader(
    ci_ledger: Connection, reader_transactions: Callable[[Connection], TransactionFactory]
) -> TransactionFactory:
    return reader_transactions(ci_ledger)


def _local_amounts(conn: Connection, company_code: str) -> list[LocalAmount]:
    rows = conn.execute(
        text(
            "SELECT fiscal_year, period, currency_lc, SUM(amount_lc) AS lc, SUM(amount_gc) AS gc "
            "FROM rpt.pnl_actual WHERE company_code = :cc AND fiscal_year = 2026 AND period <= 9 "
            "GROUP BY fiscal_year, period, currency_lc"
        ),
        {"cc": company_code},
    )
    return [LocalAmount(Period(r.fiscal_year, r.period), r.currency_lc, r.lc, r.gc) for r in rows]


def _budget_gc(conn: Connection, company_code: str) -> Decimal:
    total = conn.execute(
        text(
            "SELECT SUM(amount_gc) FROM rpt.pnl_plan WHERE plan_category = 'BUD26' "
            "AND company_code = :cc AND fiscal_year = 2026 AND period <= 9"
        ),
        {"cc": company_code},
    ).scalar_one()
    assert isinstance(total, Decimal)
    return total


def test_latest_forecast_is_fc226(tool_reader: TransactionFactory) -> None:
    with tool_reader(["CH10"]) as conn:
        versions = load_plan_versions(conn)

    resolved = resolve_version("LATEST_FC", YTD_P09, versions)

    assert resolved.plan_category == "FC226"
    assert resolved.rate_type == "F226"


def test_open_period_warning_from_period_status(tool_reader: TransactionFactory) -> None:
    with tool_reader(["CH10", "US10"]) as conn:
        statuses = load_period_statuses(conn, ["CH10", "US10"])
    ytd_p10 = resolve_periods(ViewSelection(2026, 10, PeriodView.YTD))

    assert {s.company_code for s in statuses} == {"CH10", "US10"}
    assert period_warnings(YTD_P09, ["CH10", "US10"], statuses) == []
    assert period_warnings(ytd_p10, ["CH10"], statuses) == [WarningCode.OPEN_PERIOD]


def test_ch10_constant_currency_uses_the_budget_rate(tool_reader: TransactionFactory) -> None:
    with tool_reader(["CH10"]) as conn:
        rates = load_fx_rates(conn)
        versions = load_plan_versions(conn)
        amounts = _local_amounts(conn, "CH10")
        budget_gc = _budget_gc(conn, "CH10")
        expected_cc = conn.execute(
            text(
                "SELECT SUM(a.amount_lc) * r.rate FROM rpt.pnl_actual a "
                "JOIN md.fx_rate r ON r.rate_type = 'B26' AND r.from_currency = 'CHF' "
                "AND r.to_currency = 'USD' "
                "WHERE a.company_code = 'CH10' AND a.fiscal_year = 2026 AND a.period <= 9 "
                "GROUP BY r.rate"
            )
        ).scalar_one()
    budget = resolve_version("BUD26", YTD_P09, versions)

    split = fx_split(amounts, budget_gc, budget, rates)

    assert {a.currency for a in amounts} == {"CHF"}
    assert split.actual_cc == expected_cc
    assert split.fx_effect != 0
    assert split.delta_total == split.delta_operational + split.fx_effect


def test_us10_has_no_fx_effect(tool_reader: TransactionFactory) -> None:
    with tool_reader(["US10"]) as conn:
        rates = load_fx_rates(conn)
        versions = load_plan_versions(conn)
        amounts = _local_amounts(conn, "US10")
        budget_gc = _budget_gc(conn, "US10")
    budget = resolve_version("BUD26", YTD_P09, versions)

    split = fx_split(amounts, budget_gc, budget, rates)

    assert amounts
    assert split.actual_cc == split.actual_gc
    assert split.fx_effect == 0


def test_rls_hides_other_companies_from_the_inputs(tool_reader: TransactionFactory) -> None:
    with tool_reader(["CH10"]) as conn:
        assert _local_amounts(conn, "US10") == []
