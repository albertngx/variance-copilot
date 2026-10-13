"""Budget drivers (SPEC §8.4, §8.5): volumes, prices, rates, FTEs and spend budgets."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from variance_copilot.generator.assumptions import Region, RevenueAssumptions
from variance_copilot.generator.config import LossOfExclusivity, ScenarioConfig
from variance_copilot.generator.curves import (
    lifecycle,
    loe_erosion,
    month_index,
    month_index_of,
    normalised,
)
from variance_copilot.generator.model import (
    PLAN_RATE_TYPES,
    Drivers,
    Master,
    Rates,
    Sales,
    SalesPlan,
    SpendKey,
)

PERIODS = list(range(1, 13))
REGION: dict[str, Region] = {"US10": "US", "DE10": "EU", "ES10": "EU"}
QUARTER_ENDS = (3, 6, 9, 12)
CONGRESS_ACCOUNT = "61010000"
TRIAL_ACCOUNT = "61070000"
COLLABORATION_ACCOUNT = "61080000"
POOLS: dict[str, Literal["SELLING_MARKETING", "RD", "GA"]] = {
    "F200": "SELLING_MARKETING",
    "F300": "RD",
    "F400": "GA",
}

FteKey = tuple[str, int]


@dataclass(frozen=True)
class PlanContext:
    scenario: ScenarioConfig
    master: Master
    rates: Rates
    companies: list[str]

    @property
    def revenue(self) -> RevenueAssumptions:
        return self.scenario.drivers.revenue

    def affiliates(self) -> list[str]:
        return [c for c in self.companies if c in REGION]

    def to_usd(self, version: str, company_code: str, year: int) -> float:
        currency = self.master.currency(company_code)
        return self.rates.to_usd(PLAN_RATE_TYPES[version], currency, date(year, 1, 1))


def budget_version(year: int) -> str:
    return f"BUD{year % 100:02d}"


# --- Revenue ---------------------------------------------------------------


def plan_loe(ctx: PlanContext, brand: str, company_code: str) -> date | None:
    """The LoE date the plan assumes; under E01 the budget keeps its later date."""
    for event in ctx.scenario.events:
        if isinstance(event, LossOfExclusivity) and (event.brand, event.company_code) == (
            brand,
            company_code,
        ):
            return event.budget_loe_date
    return ctx.master.loe.get((brand, ctx.master.country(company_code)))


def channels(ctx: PlanContext, company_code: str, brand: str) -> dict[str, float]:
    modality = ctx.master.brands[brand].modality
    return {h: float(s) for h, s in ctx.revenue.channel_mix[company_code][modality].items()}


def gtn_rates(ctx: PlanContext, brand: str, company_code: str, channel: str) -> dict[str, float]:
    """Deduction rates by component: brand rate x channel factor x regional split."""
    region = REGION[company_code]
    total = float(ctx.revenue.brands[brand].gtn_rate[region])
    factor = float(ctx.revenue.channel_gtn_factor[channel])
    return {c: total * factor * float(s) for c, s in ctx.revenue.gtn_split[region].items()}


def list_price(ctx: PlanContext, brand: str, company_code: str, material: str, year: int) -> float:
    assumptions = ctx.revenue.brands[brand]
    price = float(assumptions.list_price[company_code] * assumptions.skus[material].price_factor)
    change = float(ctx.revenue.annual_price_change[company_code])
    return price * (1 + change) ** (year - ctx.revenue.calibration_year)


def volume_shape(ctx: PlanContext, brand: str, company_code: str, year: int, period: int) -> float:
    """L_b(t) * E_bc(t) * S_c(m) for one month."""
    assumptions = ctx.revenue.brands[brand]
    t = month_index(year, period)
    launch_month = assumptions.launch.get(company_code)
    launch = month_index_of(launch_month) if launch_month else None
    loe_date = plan_loe(ctx, brand, company_code)
    loe = month_index_of(loe_date) if loe_date else None
    seasonality = normalised(
        [float(s) for s in ctx.revenue.seasonality[ctx.master.country(company_code)]]
    )
    return (
        lifecycle(assumptions.lifecycle, t, launch)
        * loe_erosion(assumptions.erosion, t, loe)
        * seasonality[period - 1]
    )


def _net_factor(ctx: PlanContext, brand: str, company_code: str) -> float:
    return sum(
        share * (1 - sum(gtn_rates(ctx, brand, company_code, channel).values()))
        for channel, share in channels(ctx, company_code, brand).items()
    )


def _net_sales_per_base_unit(ctx: PlanContext, brand: str, company_code: str, year: int) -> float:
    skus = ctx.revenue.brands[brand].skus
    gross = sum(
        volume_shape(ctx, brand, company_code, year, period)
        * float(sku.share)
        * list_price(ctx, brand, company_code, material, year)
        for period in PERIODS
        for material, sku in skus.items()
    )
    return gross * _net_factor(ctx, brand, company_code)


def base_volume(ctx: PlanContext, brand: str, company_code: str) -> float:
    """base_bc: monthly packs that make the plan hit its calibration target (SPEC §8.4)."""
    assumptions = ctx.revenue.brands[brand]
    if company_code in assumptions.launch_budget_lc:
        year = int(assumptions.launch[company_code][:4])
        per_unit = _net_sales_per_base_unit(ctx, brand, company_code, year)
        return float(assumptions.launch_budget_lc[company_code]) / per_unit
    region = REGION[company_code]
    target = ctx.scenario.calibration.brands[brand].by_market_musd.get(region)
    if target is None:
        return 0.0
    share = float(ctx.revenue.eu_split[company_code]) if region == "EU" else 1.0
    year = ctx.revenue.calibration_year
    to_usd = ctx.to_usd(budget_version(year), company_code, year)
    per_unit = _net_sales_per_base_unit(ctx, brand, company_code, year) * to_usd
    return float(target) * 1e6 * share / per_unit


def sales_plan(ctx: PlanContext, year: int) -> SalesPlan:
    sales: SalesPlan = {}
    for company_code in ctx.affiliates():
        for brand, assumptions in ctx.revenue.brands.items():
            base = base_volume(ctx, brand, company_code)
            for period in PERIODS:
                volume = base * volume_shape(ctx, brand, company_code, year, period)
                if volume <= 0:
                    continue
                for material, sku in assumptions.skus.items():
                    price = list_price(ctx, brand, company_code, material, year)
                    for channel, share in channels(ctx, company_code, brand).items():
                        rates = gtn_rates(ctx, brand, company_code, channel)
                        quantity = volume * float(sku.share) * share
                        sales[company_code, material, channel, period] = Sales(
                            quantity, price, rates, dict(rates)
                        )
    return sales


def net_sales(sales: SalesPlan) -> dict[tuple[str, str], float]:
    """Net sales in LC by company and material."""
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for (company_code, material, _, _), s in sales.items():
        totals[company_code, material] += s.quantity * s.list_price * (1 - sum(s.gtn.values()))
    return totals


def _quantities(sales: SalesPlan) -> dict[tuple[str, str], float]:
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for (company_code, material, _, _), s in sales.items():
        totals[company_code, material] += s.quantity
    return totals


def transfer_prices(ctx: PlanContext, sales: SalesPlan) -> dict[tuple[str, str], float]:
    """Resale-minus transfer price per pack, in the buyer's currency."""
    quantity = _quantities(sales)
    margin = ctx.revenue.affiliate_margin
    return {
        key: value / quantity[key] * (1 - float(margin[key[0]]))
        for key, value in net_sales(sales).items()
    }


def standard_costs(ctx: PlanContext, sales: SalesPlan, version: str, year: int) -> dict[str, float]:
    """Standard cost per pack in EUR: COGS ratio times the group average net price."""
    value_usd: dict[str, float] = defaultdict(float)
    quantity: dict[str, float] = defaultdict(float)
    for (company_code, material), value in net_sales(sales).items():
        value_usd[material] += value * ctx.to_usd(version, company_code, year)
    for (_, material), value in _quantities(sales).items():
        quantity[material] += value
    eur = ctx.rates.to_usd(PLAN_RATE_TYPES[version], "EUR", date(year, 1, 1))
    return {
        material: float(ctx.revenue.brands[ctx.master.brand_of(material).brand].cogs_ratio)
        * value_usd[material]
        / quantity[material]
        / eur
        for material in quantity
    }


# --- Personnel, depreciation and service costs -----------------------------


def fte_plan(ctx: PlanContext, year: int) -> dict[FteKey, float]:
    personnel = ctx.scenario.drivers.personnel
    fte = {}
    for cost_center, base in personnel.fte.items():
        company_code = ctx.master.cost_centers[cost_center].company_code
        if company_code not in ctx.companies:
            continue
        growth = float(personnel.annual_growth[company_code])
        for period in PERIODS:
            fte[cost_center, period] = float(base) * (1 + growth) ** (
                month_index(year, period) / 12
            )
    return fte


def salaries(ctx: PlanContext, cost_center: str, fte: float) -> float:
    company_code = ctx.master.cost_centers[cost_center].company_code
    return fte * float(ctx.scenario.calibration.cost_per_fte_lc[company_code]) / 12


def personnel_cost(ctx: PlanContext, cost_center: str, fte: float) -> float:
    """Monthly salaries, social security and bonus accrual in LC."""
    personnel = ctx.scenario.drivers.personnel
    company_code = ctx.master.cost_centers[cost_center].company_code
    loading = float(personnel.social_rate[company_code] + personnel.bonus_rate[company_code])
    return salaries(ctx, cost_center, fte) * (1 + loading)


def company_fte(master: Master, fte: Mapping[FteKey, float]) -> dict[FteKey, float]:
    totals: dict[FteKey, float] = defaultdict(float)
    for (cost_center, period), value in fte.items():
        totals[master.cost_centers[cost_center].company_code, period] += value
    return totals


def depreciation_plan(ctx: PlanContext, fte: Mapping[FteKey, float]) -> dict[FteKey, float]:
    """Straight-line depreciation of a per-FTE asset register, fixed for the year."""
    rates = ctx.scenario.drivers.spend.depreciation_per_fte
    depreciation = {}
    for cost_center, period in fte:
        category = ctx.master.cost_centers[cost_center].category
        rate = float(rates.get(category, rates["DEFAULT"]))
        depreciation[cost_center, period] = rate * fte[cost_center, 1] / 12
    return depreciation


def is_service(cost_center: str) -> bool:
    return "-SVC-" in cost_center


def service_spend(ctx: PlanContext, fte: Mapping[FteKey, float]) -> dict[SpendKey, float]:
    totals = company_fte(ctx.master, fte)
    spend: dict[SpendKey, float] = {}
    for service in ctx.scenario.drivers.spend.service_costs:
        company_code = ctx.master.cost_centers[service.cost_center].company_code
        if company_code in ctx.companies:
            for period in PERIODS:
                amount = float(service.per_fte) * totals[company_code, period] / 12
                spend[service.cost_center, service.gl_account, period, None] = amount
    return spend


def allocation_receivers(master: Master, company_code: str, staffed: Iterable[str]) -> list[str]:
    """Non-service cost centers with staff, which receive service cost assessments."""
    staffed = set(staffed)
    return [
        cc.cost_center
        for cc in master.cost_centers_of(company_code)
        if not is_service(cc.cost_center) and cc.cost_center in staffed
    ]


# --- Spend budgets ---------------------------------------------------------


@dataclass
class _Budget:
    """Running spend budget for one version and year."""

    ctx: PlanContext
    version: str
    year: int
    spend: dict[SpendKey, float]

    def lc(self, company_code: str, usd: float) -> float:
        return usd / self.ctx.to_usd(self.version, company_code, self.year)

    def add(self, key: SpendKey, amount: float) -> None:
        self.spend[key] = self.spend.get(key, 0.0) + amount

    def spread(self, cost_center: str, accounts: Mapping[str, Decimal], annual_usd: float) -> None:
        company_code = self.ctx.master.cost_centers[cost_center].company_code
        annual = self.lc(company_code, annual_usd)
        spend = self.ctx.scenario.drivers.spend
        for gl_account, share in accounts.items():
            weights = [
                float(spend.congress_weight)
                if gl_account == CONGRESS_ACCOUNT and period in spend.congress_months
                else 1.0
                for period in PERIODS
            ]
            for period, weight in zip(PERIODS, weights, strict=True):
                amount = annual * float(share) * weight / sum(weights)
                self.add((cost_center, gl_account, period, None), amount)


def fixed_cost_by_area(
    ctx: PlanContext,
    version: str,
    year: int,
    fte: Mapping[FteKey, float],
    depreciation: Mapping[FteKey, float],
    services: Mapping[SpendKey, float],
) -> dict[str, float]:
    """Annual USD personnel, depreciation and service cost by functional area, after allocation."""
    cost: dict[str, float] = defaultdict(float)
    for (cost_center, period), value in fte.items():
        cost[cost_center] += personnel_cost(ctx, cost_center, value)
        cost[cost_center] += depreciation[cost_center, period]
    for (cost_center, _, _, _), value in services.items():
        cost[cost_center] += value

    by_area: dict[str, float] = defaultdict(float)
    for cost_center, value in cost.items():
        center = ctx.master.cost_centers[cost_center]
        usd = value * ctx.to_usd(version, center.company_code, year)
        if not is_service(cost_center):
            by_area[center.functional_area] += usd
            continue
        receivers = allocation_receivers(ctx.master, center.company_code, (cc for cc, _ in fte))
        total = sum(fte[r, 1] for r in receivers)
        for receiver in receivers:
            area = ctx.master.cost_centers[receiver].functional_area
            by_area[area] += usd * fte[receiver, 1] / total
    return by_area


def trial_intensity(ctx: PlanContext, wbs_element: str, t: int) -> float:
    """Relative monthly cost of a clinical trial along its phase curve."""
    trial = ctx.scenario.drivers.spend.trials[wbs_element]
    start = month_index_of(trial.start)
    if not start <= t < start + trial.months:
        return 0.0
    position = (t - start + 0.5) / trial.months
    elapsed = 0.0
    for phase in ctx.scenario.drivers.spend.trial_phases:
        elapsed += float(phase.duration)
        if position <= elapsed:
            return float(trial.weight * phase.cost / phase.duration)
    return 0.0


def _selling_marketing(
    budget: _Budget, pool: float, net: dict[tuple[str, str], float], net_usd: dict[str, float]
) -> None:
    ctx, master = budget.ctx, budget.ctx.master
    sm = ctx.scenario.drivers.spend.selling_marketing
    if "CH10" in ctx.companies:
        budget.spread("CH10-MKT-GLB", sm.accounts["MARKETING"], pool * float(sm.global_share))
        pool *= 1 - float(sm.global_share)
    group_net = sum(net_usd.values())
    for company_code in ctx.affiliates():
        ta_net: dict[str, float] = defaultdict(float)
        for (c, material), value in net.items():
            if c == company_code:
                ta_net[master.brand_of(material).therapeutic_area] += value
        centers = master.cost_centers_of(company_code, "MARKETING", "SALES")
        weights = {
            cc.cost_center: float(sm.category_weight[cc.category])
            * ta_net.get(cc.cost_center.rsplit("-", 1)[1], 0.0)
            for cc in centers
        }
        company_pool = pool * net_usd[company_code] / group_net
        for cc in centers:
            if weights[cc.cost_center]:
                share = weights[cc.cost_center] / sum(weights.values())
                budget.spread(cc.cost_center, sm.accounts[cc.category], company_pool * share)


def _research_development(
    budget: _Budget, pool: float, fte: Mapping[FteKey, float], net_usd: dict[str, float]
) -> None:
    ctx, master = budget.ctx, budget.ctx.master
    spend, rd = ctx.scenario.drivers.spend, ctx.scenario.drivers.spend.research_development
    if "CH10" in ctx.companies:
        intensity = {
            (wbs, period): trial_intensity(ctx, wbs, month_index(budget.year, period))
            for wbs in spend.trials
            for period in PERIODS
        }
        total = sum(intensity.values())
        for (wbs, period), weight in intensity.items():
            if weight:
                cost_center = master.wbs[wbs].cost_center
                amount = budget.lc("CH10", pool * float(rd.trials) * weight / total)
                budget.add((cost_center, TRIAL_ACCOUNT, period, wbs), amount)
        collaboration = budget.lc("CH10", pool * float(rd.collaborations)) / len(QUARTER_ENDS)
        for period in QUARTER_ENDS:
            key = (
                rd.collaboration_cost_center,
                COLLABORATION_ACCOUNT,
                period,
                rd.collaboration_wbs,
            )
            budget.add(key, collaboration)
        research = master.cost_centers_of("CH10", "DEVELOPMENT", "RESEARCH")
        research_fte = sum(fte[cc.cost_center, 1] for cc in research)
        for cc in research:
            share = fte[cc.cost_center, 1] / research_fte
            budget.spread(cc.cost_center, rd.other_accounts, pool * float(rd.other) * share)
    group_net = sum(net_usd.values())
    for company_code in ctx.affiliates():
        medical = master.cost_centers_of(company_code, "MEDICAL")
        for cc in medical:
            share = net_usd[company_code] / group_net / len(medical)
            budget.spread(cc.cost_center, rd.medical_accounts, pool * float(rd.medical) * share)


def _general_admin(budget: _Budget, pool: float, fte: Mapping[FteKey, float]) -> None:
    ctx = budget.ctx
    admin = [
        cc.cost_center
        for company_code in ctx.companies
        for cc in ctx.master.cost_centers_of(company_code, "ADMINISTRATION")
    ]
    total = sum(fte[cc, 1] for cc in admin)
    for cost_center in admin:
        share = fte[cost_center, 1] / total
        budget.spread(cost_center, ctx.scenario.drivers.spend.general_admin.accounts, pool * share)


def spend_plan(
    ctx: PlanContext,
    version: str,
    year: int,
    sales: SalesPlan,
    fte: Mapping[FteKey, float],
    depreciation: Mapping[FteKey, float],
) -> dict[SpendKey, float]:
    """Service costs plus top-down opex budgets sized to the §8.4 cost structure."""
    budget = _Budget(ctx, version, year, service_spend(ctx, fte))
    net = net_sales(sales)
    net_usd: dict[str, float] = defaultdict(float)
    for (company_code, _), value in net.items():
        net_usd[company_code] += value * ctx.to_usd(version, company_code, year)
    fixed = fixed_cost_by_area(ctx, version, year, fte, depreciation, budget.spend)
    targets = ctx.scenario.calibration.cost_structure
    group_net = sum(net_usd.values())
    pools = {
        area: max(0.0, float(targets[key]) * group_net - fixed[area]) for area, key in POOLS.items()
    }
    _selling_marketing(budget, pools["F200"], net, net_usd)
    _research_development(budget, pools["F300"], fte, net_usd)
    _general_admin(budget, pools["F400"], fte)
    return budget.spend


def budget_drivers(ctx: PlanContext, year: int) -> Drivers:
    version = budget_version(year)
    sales = sales_plan(ctx, year)
    fte = fte_plan(ctx, year)
    depreciation = depreciation_plan(ctx, fte)
    return Drivers(
        version=version,
        fiscal_year=year,
        periods=PERIODS,
        sales=sales,
        transfer_price=transfer_prices(ctx, sales),
        standard_cost=standard_costs(ctx, sales, version, year),
        fte=fte,
        spend=spend_plan(ctx, version, year, sales, fte, depreciation),
        depreciation=depreciation,
    )
