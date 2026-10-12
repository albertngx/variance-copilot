"""Plan versions, plan lines and statistical key figures (SPEC §7.9, §8.3 step 8)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from variance_copilot.generator.fx import GROUP_CURRENCY
from variance_copilot.generator.master import CHART_OF_ACCOUNTS
from variance_copilot.generator.model import PLAN_RATE_TYPES, Drivers, Entry, Master, Rates
from variance_copilot.generator.postings import MILLI, Row, cents

APPROVAL_ZONE = ZoneInfo("Europe/Zurich")
PLAN_VERSIONS = (
    ("BUD25", 2025, "BUDGET", "Budget 2025", 0, date(2024, 11, 29)),
    ("BUD26", 2026, "BUDGET", "Budget 2026", 0, date(2025, 11, 28)),
    ("FC126", 2026, "FORECAST", 'Forecast 1 2026 ("3+9")', 3, date(2026, 4, 17)),
    ("FC226", 2026, "FORECAST", 'Forecast 2 2026 ("6+6")', 6, date(2026, 7, 17)),
)
DIMENSIONS = (
    "company_code", "period", "gl_account", "cost_center", "profit_center", "functional_area",
    "segment", "trading_partner", "material", "channel", "wbs_element",
)  # fmt: skip


def plan_versions(years: Iterable[int]) -> list[Row]:
    wanted = set(years)
    return [
        {
            "plan_category": category,
            "fiscal_year": year,
            "kind": kind,
            "description": description,
            "actuals_through_period": through,
            "fx_rate_type": PLAN_RATE_TYPES[category],
            "approved_at": datetime.combine(approved, time(12), APPROVAL_ZONE),
        }
        for category, year, kind, description, through, approved in PLAN_VERSIONS
        if year in wanted
    ]


def plan_lines(
    drivers: Drivers, entries: Iterable[Entry], master: Master, rates: Rates
) -> list[Row]:
    """P&L lines of the plan entries, aggregated by dimension, GC at the version's rate type."""
    rate_type = PLAN_RATE_TYPES[drivers.version]
    valid = date(drivers.fiscal_year, 1, 1)
    totals: dict[tuple[str | int | None, ...], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for entry in entries:
        local = master.currency(entry.company_code)
        to_local = rates.to_usd(rate_type, entry.currency, valid) / rates.to_usd(
            rate_type, local, valid
        )
        to_group = rates.to_usd(rate_type, local, valid)
        for line in entry.lines:
            if master.accounts[line.gl_account].account_type == "X":
                continue
            profit_center, functional_area, segment = master.derive(entry.company_code, line)
            key: tuple[str | int | None, ...] = (
                entry.company_code, entry.period, line.gl_account, line.cost_center, profit_center,
                functional_area, segment, line.trading_partner, line.material, line.channel,
                line.wbs_element,
            )  # fmt: skip
            amount_lc = line.amount * to_local
            total = totals[key]
            total[0] += amount_lc
            total[1] += amount_lc * to_group
            total[2] += line.quantity or 0.0
    rows = []
    for key, (amount_lc, amount_gc, quantity) in totals.items():
        if not cents(amount_lc):
            continue
        row: Row = dict(zip(DIMENSIONS, key, strict=True))
        row.update(
            plan_category=drivers.version,
            fiscal_year=drivers.fiscal_year,
            chart_of_accounts=CHART_OF_ACCOUNTS,
            amount_lc=cents(amount_lc),
            currency_lc=master.currency(str(key[0])),
            amount_gc=cents(amount_gc),
            currency_gc=GROUP_CURRENCY,
            quantity=Decimal(repr(quantity)).quantize(MILLI) if quantity else None,
            unit="PK" if quantity else None,
        )
        rows.append(row)
    return rows


def stat_key_figures(drivers: Drivers, master: Master, fte_per_head: Decimal) -> list[Row]:
    """FTE and HEADCOUNT per cost center and period."""
    rows = []
    for (cost_center, period), fte in sorted(drivers.fte.items()):
        base = {
            "version": drivers.version,
            "company_code": master.cost_centers[cost_center].company_code,
            "fiscal_year": drivers.fiscal_year,
            "period": period,
            "cost_center": cost_center,
        }
        rows.append({**base, "key_figure": "FTE", "value": cents(fte)})
        rows.append(
            {**base, "key_figure": "HEADCOUNT", "value": Decimal(round(fte / float(fte_per_head)))}
        )
    return rows
