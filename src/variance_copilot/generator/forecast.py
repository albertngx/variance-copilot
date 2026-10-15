"""Forecast drivers FC126 and FC226 (SPEC §7.9, §8.10)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace

from variance_copilot.generator.actual import Layer
from variance_copilot.generator.model import Drivers

CUT_OFFS = {"FC126": 3, "FC226": 6}
TRAILING_MONTHS = 3
PHASED_ACCOUNTS = ("61070000", "61080000")  # trial curves and quarterly collaborations


def forecast_drivers(
    budget: Drivers, actual: Drivers, version: str, layers: Sequence[Layer]
) -> Drivers:
    """Budget drivers for the months after the cut-off, re-planned with what was known then."""
    cut = CUT_OFFS[version]
    trailing = range(cut - TRAILING_MONTHS + 1, cut + 1)
    run_rate: dict[tuple[str, str, str | None], float] = defaultdict(float)
    for (cost_center, gl_account, period, wbs), amount in actual.spend.items():
        if period in trailing:
            run_rate[cost_center, gl_account, wbs] += amount / TRAILING_MONTHS

    spend = {}
    for key, amount in budget.spend.items():
        cost_center, gl_account, period, wbs = key
        if period <= cut:
            continue
        rate = run_rate.get((cost_center, gl_account, wbs))
        if rate is None or gl_account in PHASED_ACCOUNTS:
            spend[key] = amount
        else:
            spend[key] = 0.5 * amount + 0.5 * rate

    staffing = {
        cost_center: actual.fte[cost_center, cut] / budget.fte[cost_center, cut]
        for cost_center, period in budget.fte
        if period == cut and (cost_center, cut) in actual.fte
    }
    fte = {key: value for key, value in actual.fte.items() if key[1] <= cut}
    fte |= {
        (cost_center, period): value * staffing.get(cost_center, 1.0)
        for (cost_center, period), value in budget.fte.items()
        if period > cut
    }
    drivers = replace(
        budget,
        version=version,
        periods=[p for p in budget.periods if p > cut],
        sales={key: s for key, s in budget.sales.items() if key[3] > cut},
        spend=spend,
        fte=fte,
        depreciation={key: v for key, v in budget.depreciation.items() if key[1] > cut},
    )
    for layer in layers:
        drivers = layer.apply(drivers)
    return drivers
