from __future__ import annotations

from collections import defaultdict

import pytest

from variance_copilot.generator.model import Master, Rates
from variance_copilot.generator.pipeline import Ledger, build_ledger, prepare
from variance_copilot.generator.plan import (
    PlanContext,
    gtn_rates,
    net_sales,
    plan_loe,
    volume_shape,
)
from variance_copilot.generator.plan_lines import plan_lines


@pytest.fixture(scope="module")
def dev() -> Ledger:
    return build_ledger(prepare("dev"))


@pytest.fixture(scope="module")
def ctx(dev: Ledger) -> PlanContext:
    run = prepare("dev")
    return PlanContext(
        run.scenario, Master(dev.master), Rates(dev.fx_rates), run.profile.company_codes
    )


def test_gtn_split_adds_up_to_the_brand_rate(ctx: PlanContext) -> None:
    rates = gtn_rates(ctx, "LUMORA", "US10", "WHOLESALER")

    assert set(rates) == {"COMMERCIAL", "GOVERNMENT", "RETURNS", "DISCOUNT"}
    assert sum(rates.values()) == pytest.approx(0.50)
    assert sum(gtn_rates(ctx, "LUMORA", "US10", "SPECIALTY_PHARMACY").values()) == pytest.approx(
        0.55
    )


def test_budget_assumes_cardivex_us_loe_in_september_2026(ctx: PlanContext) -> None:
    assert plan_loe(ctx, "CARDIVEX", "US10").isoformat() == "2026-09-01"
    assert plan_loe(ctx, "CARDIVEX", "DE10").isoformat() == "2028-07-01"
    august, september = (volume_shape(ctx, "CARDIVEX", "US10", 2026, p) for p in (8, 9))
    assert september < 0.8 * august


def test_fy2025_budget_hits_calibration_targets(dev: Ledger, ctx: PlanContext) -> None:
    budget = next(b for b in dev.budgets if b.fiscal_year == 2025)
    by_brand: dict[str, float] = defaultdict(float)
    for (company_code, material), value in net_sales(budget.sales).items():
        usd = value * ctx.to_usd("BUD25", company_code, 2025)
        by_brand[ctx.master.brand_of(material).brand] += usd / 1e6

    for brand, target in ctx.scenario.calibration.brands.items():
        assert by_brand[brand] == pytest.approx(float(target.net_sales_musd), rel=1e-6)


@pytest.mark.parametrize("version", ["BUD25", "BUD26"])
def test_budget_cost_structure_within_three_points(
    dev: Ledger, ctx: PlanContext, version: str
) -> None:
    budget = next(b for b in dev.budgets if b.version == version)
    by_area: dict[str, float] = defaultdict(float)
    for row in plan_lines(budget, dev.plan_entries[version], ctx.master, ctx.rates):
        if not row["trading_partner"]:
            by_area[row["functional_area"]] -= float(row["amount_gc"])
    net = by_area["F010"]
    targets = ctx.scenario.calibration.cost_structure
    shares = {"COGS": "F100", "SELLING_MARKETING": "F200", "RD": "F300", "GA": "F400"}

    for key, area in shares.items():
        assert -by_area[area] / net == pytest.approx(float(targets[key]), abs=0.03)
