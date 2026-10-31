from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Connection, create_engine, text

from variance_copilot.db import sqlalchemy_url
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
from variance_copilot.generator.pipeline import prepare
from variance_copilot.generator.seed import run_seed

YTD_P09 = resolve_periods(ViewSelection(2026, 9, PeriodView.YTD))


@pytest.fixture(scope="module")
def ledger(migrated_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Connection]:
    """The ci profile seeded inside a transaction that is rolled back afterwards."""
    engine = create_engine(sqlalchemy_url(migrated_url))
    with engine.connect() as conn:
        transaction = conn.begin()
        run_seed(conn, prepare("ci", 20261010), Path(tmp_path_factory.mktemp("generated")))
        yield conn
        transaction.rollback()
    engine.dispose()


@contextmanager
def tool_reader(conn: Connection, company_scope: Sequence[str]) -> Iterator[Connection]:
    """The tool role and company scope, undone with a savepoint."""
    savepoint = conn.begin_nested()
    try:
        conn.execute(text("SET LOCAL ROLE vc_tool_reader"))
        conn.execute(
            text("SELECT set_config('app.company_scope', :scope, true)"),
            {"scope": ",".join(company_scope)},
        )
        yield conn
    finally:
        savepoint.rollback()


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


def test_latest_forecast_is_fc226(ledger: Connection) -> None:
    with tool_reader(ledger, ["CH10"]) as conn:
        versions = load_plan_versions(conn)

    resolved = resolve_version("LATEST_FC", YTD_P09, versions)

    assert resolved.plan_category == "FC226"
    assert resolved.rate_type == "F226"


def test_open_period_warning_from_period_status(ledger: Connection) -> None:
    with tool_reader(ledger, ["CH10", "US10"]) as conn:
        statuses = load_period_statuses(conn, ["CH10", "US10"])
    ytd_p10 = resolve_periods(ViewSelection(2026, 10, PeriodView.YTD))

    assert {s.company_code for s in statuses} == {"CH10", "US10"}
    assert period_warnings(YTD_P09, ["CH10", "US10"], statuses) == []
    assert period_warnings(ytd_p10, ["CH10"], statuses) == [WarningCode.OPEN_PERIOD]


def test_ch10_constant_currency_uses_the_budget_rate(ledger: Connection) -> None:
    with tool_reader(ledger, ["CH10"]) as conn:
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


def test_us10_has_no_fx_effect(ledger: Connection) -> None:
    with tool_reader(ledger, ["US10"]) as conn:
        rates = load_fx_rates(conn)
        versions = load_plan_versions(conn)
        amounts = _local_amounts(conn, "US10")
        budget_gc = _budget_gc(conn, "US10")
    budget = resolve_version("BUD26", YTD_P09, versions)

    split = fx_split(amounts, budget_gc, budget, rates)

    assert amounts
    assert split.actual_cc == split.actual_gc
    assert split.fx_effect == 0


def test_rls_hides_other_companies_from_the_inputs(ledger: Connection) -> None:
    with tool_reader(ledger, ["CH10"]) as conn:
        assert _local_amounts(conn, "US10") == []
