"""Integrity rules I-01 to I-08 (SPEC §7.12) on the generated ledger, in memory."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

import pytest

from variance_copilot.generator.calendar import fiscal_periods, period_statuses
from variance_copilot.generator.model import Master, Rates
from variance_copilot.generator.pipeline import Ledger, build_ledger, prepare
from variance_copilot.generator.plan_lines import plan_lines, plan_versions, stat_key_figures

PROFILES = ("ci", "dev")
IC_REVENUE = ("40010000", "40600000")
IC_EXPENSE = ("50060000", "61090000")


@pytest.fixture(scope="module", params=PROFILES)
def ledger(request: pytest.FixtureRequest) -> tuple[str, Ledger]:
    return request.param, build_ledger(prepare(request.param))


def _documents(ledger: Ledger) -> dict[tuple[str, int, str], list[dict]]:
    documents: dict[tuple[str, int, str], list[dict]] = defaultdict(list)
    for line in ledger.journal.lines:
        documents[line["company_code"], line["fiscal_year"], line["document_number"]].append(line)
    return documents


def test_every_document_balances(ledger: tuple[str, Ledger]) -> None:
    for key, lines in _documents(ledger[1]).items():
        for column in ("amount_tc", "amount_lc", "amount_gc"):
            assert sum(line[column] for line in lines) == 0, (key, column)


def test_debit_credit_indicator_matches_sign(ledger: tuple[str, Ledger]) -> None:
    for line in ledger[1].journal.lines:
        assert (line["debit_credit"] == "S") == (line["amount_lc"] > 0 or line["amount_tc"] > 0)


def test_no_entry_after_the_period_closed(ledger: tuple[str, Ledger]) -> None:
    name, generated = ledger
    run = prepare(name)
    statuses = {
        (s.company_code, s.fiscal_year, s.period): s
        for s in period_statuses(
            run.scenario.calendar,
            run.profile.company_codes,
            run.profile.fiscal_years,
            run.scenario.as_of,
        )
    }
    for header in generated.journal.headers:
        status = statuses[header["company_code"], header["fiscal_year"], header["period"]]
        assert status.closed_at is None or header["entry_ts"] <= status.closed_at
        assert header["entry_ts"].date() <= run.scenario.as_of


def test_posting_dates_inside_their_period(ledger: tuple[str, Ledger]) -> None:
    periods = {(p.fiscal_year, p.period): p for p in fiscal_periods([2025, 2026])}
    for header in ledger[1].journal.headers:
        period = periods[header["fiscal_year"], header["period"]]
        assert period.start_date <= header["posting_date"] <= period.end_date


def test_every_accrual_is_reversed_on_day_one_of_the_next_period(
    ledger: tuple[str, Ledger],
) -> None:
    headers = {
        (h["company_code"], h["fiscal_year"], h["document_number"]): h
        for h in ledger[1].journal.headers
    }
    accruals = [
        h for h in headers.values() if (h["header_text"] or "").startswith("Accrued services")
    ]
    assert accruals
    for accrual in accruals:
        reversal = headers[
            accrual["company_code"], accrual["reversal_fiscal_year"], accrual["reversal_document"]
        ]
        assert reversal["is_reversal"]
        assert reversal["reversal_document"] == accrual["document_number"]
        month_end = accrual["posting_date"].replace(day=28) + timedelta(days=4)
        assert reversal["posting_date"] == month_end.replace(day=1)
    for reversal in (h for h in headers.values() if h["is_reversal"]):
        assert (
            reversal["company_code"],
            reversal["reversal_fiscal_year"],
            reversal["reversal_document"],
        ) in headers


def test_intercompany_revenue_equals_buyer_expense(ledger: tuple[str, Ledger]) -> None:
    revenue: dict[tuple[str, str, int, int], Decimal] = defaultdict(Decimal)
    expense: dict[tuple[str, str, int, int], Decimal] = defaultdict(Decimal)
    for line in ledger[1].journal.lines:
        when = (line["fiscal_year"], line["period"])
        if line["gl_account"] in IC_REVENUE:
            revenue[line["company_code"], line["trading_partner"], *when] -= line["amount_gc"]
        elif line["gl_account"] in IC_EXPENSE:
            expense[line["trading_partner"], line["company_code"], *when] += line["amount_gc"]
    assert revenue
    assert set(revenue) == set(expense)
    for key, value in revenue.items():
        assert abs(value - expense[key]) <= 1, key


def test_no_intercompany_inventory_build(ledger: tuple[str, Ledger]) -> None:
    inventory: dict[tuple[str, int, int], Decimal] = defaultdict(Decimal)
    for line in ledger[1].journal.lines:
        if line["gl_account"] == "12000000" and line["company_code"] != "IE10":
            inventory[line["company_code"], line["fiscal_year"], line["period"]] += line[
                "amount_lc"
            ]
    assert inventory
    assert all(abs(value) <= 1 for value in inventory.values())  # translation rounding only


def test_functional_area_follows_derivation(ledger: tuple[str, Ledger]) -> None:
    master = Master(ledger[1].master)
    for line in ledger[1].journal.lines:
        if line["cost_center"]:
            expected = master.cost_centers[line["cost_center"]].functional_area
        else:
            expected = master.accounts[line["gl_account"]].default_functional_area
        assert line["functional_area"] == expected
        if master.accounts[line["gl_account"]].account_type != "X":
            assert line["functional_area"] is not None


def test_billing_is_monthly_in_ci_and_weekly_in_full() -> None:
    def billing_days(name: str) -> dict[tuple[str, int], set]:
        days: dict[tuple[str, int], set] = defaultdict(set)
        for header in build_ledger(prepare(name)).journal.headers:
            if header["document_type"] == "RV":
                days[header["company_code"], header["period"]].add(header["posting_date"])
        return days

    assert {len(d) for d in billing_days("ci").values()} == {1}
    weekly = billing_days("full")
    assert min(len(d) for d in weekly.values()) >= 4
    assert all(day.weekday() == 4 for d in weekly.values() for day in d)


def test_ci_profile_size() -> None:
    lines = len(build_ledger(prepare("ci")).journal.lines)
    assert 10_000 <= lines <= 15_000


def test_generation_is_deterministic() -> None:
    first, second = build_ledger(prepare("ci")), build_ledger(prepare("ci"))

    assert first.journal.headers == second.journal.headers
    assert first.journal.lines == second.journal.lines


def test_plan_lines_use_the_version_rate(ledger: tuple[str, Ledger]) -> None:
    generated = ledger[1]
    master, rates = Master(generated.master), Rates(generated.fx_rates)
    for budget in generated.budgets:
        rows = plan_lines(budget, generated.plan_entries[budget.version], master, rates)
        rate_type = "B" + budget.version[3:]
        for row in rows:
            rate = rates.to_usd(rate_type, row["currency_lc"], date(budget.fiscal_year, 1, 1))
            assert abs(row["amount_gc"] - row["amount_lc"] * Decimal(repr(rate))) <= Decimal("0.02")
            assert master.accounts[row["gl_account"]].account_type != "X"


def test_plan_versions_and_key_figures(ledger: tuple[str, Ledger]) -> None:
    name, generated = ledger
    run = prepare(name)
    versions = {row["plan_category"]: row for row in plan_versions(run.profile.fiscal_years)}
    expected = {"BUD26", "FC126", "FC226"} | (
        {"BUD25"} if 2025 in run.profile.fiscal_years else set()
    )
    assert set(versions) == expected
    assert versions["FC226"]["actuals_through_period"] == 6
    assert versions["FC126"]["fx_rate_type"] == "F126"

    master = Master(generated.master)
    per_head = run.scenario.drivers.personnel.fte_per_head
    for drivers in [*generated.budgets, *generated.actuals]:
        figures = stat_key_figures(drivers, master, per_head)
        assert {f["key_figure"] for f in figures} == {"FTE", "HEADCOUNT"}
        assert {f["version"] for f in figures} == {drivers.version}


def test_fy2025_audit_adjustments_post_to_period_13(ledger: tuple[str, Ledger]) -> None:
    name, generated = ledger
    special = [h for h in generated.journal.headers if h["period"] == 13]
    if name == "ci":
        assert special == []
        return
    assert {h["company_code"] for h in special} == set(prepare(name).profile.company_codes)
    assert {(h["fiscal_year"], h["posting_date"], h["document_type"]) for h in special} == {
        (2025, date(2025, 12, 31), "SA")
    }
    assert all(h["entry_ts"].year == 2026 for h in special)
