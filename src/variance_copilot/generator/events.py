"""Scenario events (SPEC §8.8, §8.10): driver layers and event documents."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date

from variance_copilot.config import load_materiality
from variance_copilot.generator.actual import Layer
from variance_copilot.generator.config import (
    CapitalisedMilestone,
    CutOffError,
    Decoys,
    Divestment,
    Event,
    EventDocument,
    FxShock,
    GtnTrueUp,
    InventoryWriteOff,
    LaunchOutperformance,
    LossOfExclusivity,
    Misposting,
    RdAcceleration,
    Restructuring,
    ScenarioConfig,
    TransferPriceChange,
)
from variance_copilot.generator.curves import loe_erosion, month_index, month_index_of
from variance_copilot.generator.model import Drivers, Entry, Line, Sales, SpendKey
from variance_copilot.generator.plan import PlanContext, is_service, personnel_cost, plan_loe
from variance_copilot.generator.rng import module_rng
from variance_copilot.generator.streams import (
    AP,
    INVENTORY,
    NAMED_USERS,
    LedgerContext,
    sales_items,
)

FX_EVENT = "E02"
DECOY_EVENT = "E12"
NET_SALES = ("GROSS_SALES", "GTN_DEDUCTION")
OPEX = ("SELLING_MARKETING", "RESEARCH_DEVELOPMENT", "GENERAL_ADMIN")
COSTS = ("COGS", *OPEX, "OTHER_EXPENSE")
ALL_COMPANIES = ("CH10", "DE10", "ES10", "IE10", "US10")
BANK = "29000000"


@dataclass(frozen=True)
class Measure:
    """The P&L slice an event's impact is measured on."""

    company_codes: tuple[str, ...]
    pnl_lines: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    brand: str | None = None
    third_party_only: bool = False


@dataclass(frozen=True)
class Decoy:
    cost_center: str
    gl_account: str
    period: int
    amount_lc: float
    amount_usd: float


def active_events(
    scenario: ScenarioConfig, wanted: Iterable[str], exclude: Iterable[str] = ()
) -> list[str]:
    wanted, excluded = set(wanted), set(exclude)
    return [e.id for e in scenario.events if e.id in wanted and e.id not in excluded]


def known_events(scenario: ScenarioConfig, active: Iterable[str], version: str) -> list[str]:
    return [e for e in active if version in scenario.event(e).known_by]


def measure(scenario: ScenarioConfig, event_id: str) -> Measure:
    event = scenario.event(event_id)
    match event:
        case LossOfExclusivity() | GtnTrueUp():
            return Measure((event.company_code,), categories=NET_SALES, brand=event.brand,
                           third_party_only=True)  # fmt: skip
        case LaunchOutperformance():
            return Measure(tuple(event.launch_period), categories=NET_SALES, brand=event.brand,
                           third_party_only=True)  # fmt: skip
        case InventoryWriteOff():
            return Measure((event.company_code,), pnl_lines=("COGS",))
        case Misposting() | RdAcceleration():
            return Measure((event.company_code,), pnl_lines=("RESEARCH_DEVELOPMENT",))
        case CutOffError():
            return Measure((event.company_code,), pnl_lines=("SELLING_MARKETING",))
        case FxShock() | CapitalisedMilestone():
            return Measure((event.company_code,), pnl_lines=COSTS)
        case Restructuring():
            companies = tuple(p.company_code for p in event.provisions)
            return Measure(companies, pnl_lines=("OTHER_EXPENSE",))
        case TransferPriceChange():
            return Measure((event.buyer,), pnl_lines=("COGS",))
        case Divestment():
            return Measure((event.company_code,), pnl_lines=("OTHER_INCOME",))
        case Decoys():
            return Measure(ALL_COMPANIES, pnl_lines=OPEX)
    raise NotImplementedError(event_id)


def event_year(scenario: ScenarioConfig, event: Event) -> int:
    year = getattr(event, "fiscal_year", None)
    return int(year) if year else scenario.as_of.year


# --- Driver layers ---------------------------------------------------------


def _scale_sales(drivers: Drivers, factor: Mapping[tuple[str, str, str, int], float]) -> Drivers:
    sales = {
        key: replace(s, quantity=s.quantity * factor[key]) if key in factor else s
        for key, s in drivers.sales.items()
    }
    return replace(drivers, sales=sales)


def _add_spend(drivers: Drivers, changes: Mapping[SpendKey, float], event_id: str) -> Drivers:
    spend = dict(drivers.spend)
    for key, change in changes.items():
        if key[2] not in drivers.periods:
            continue
        value = spend.get(key, 0.0) + change
        if value < 0:
            raise ValueError(f"{event_id}: budgeted spend on {key} is below the event amount")
        spend[key] = value
    return replace(drivers, spend=spend)


def _loss_of_exclusivity(event: LossOfExclusivity, plan: PlanContext) -> Layer:
    """Erosion against the pre-LoE baseline from the actual LoE date, plus a net price cut."""
    erosion = plan.revenue.brands[event.brand].erosion
    budget_loe = plan_loe(plan, event.brand, event.company_code)
    loe = month_index_of(budget_loe) if budget_loe else None
    observed = sorted(event.volume_erosion_vs_baseline.items())

    def observed_erosion(year: int, period: int) -> float | None:
        month = f"{year}-{period:02d}"
        known = [value for key, value in observed if key <= month]
        return float(known[-1]) if known else None

    def apply(drivers: Drivers) -> Drivers:
        sales = dict(drivers.sales)
        for key, s in drivers.sales.items():
            company_code, material, _, period = key
            brand = plan.master.brand_of(material).brand
            if (company_code, brand) != (event.company_code, event.brand):
                continue
            quantity, gtn, gtn_actual = s.quantity, dict(s.gtn), dict(s.gtn_actual)
            change = observed_erosion(drivers.fiscal_year, period)
            if change is not None:
                planned = loe_erosion(erosion, month_index(drivers.fiscal_year, period), loe)
                quantity = quantity / planned * (1 + change)
            if date(drivers.fiscal_year, period, 1) >= event.net_price_change.from_:
                cut = -(1 - sum(s.gtn.values())) * float(event.net_price_change.change)
                gtn["COMMERCIAL"] += cut
                gtn_actual["COMMERCIAL"] += cut
            sales[key] = Sales(quantity, s.list_price, gtn, gtn_actual)
        return replace(drivers, sales=sales)

    return Layer(event.id, apply)


def _launch(event: LaunchOutperformance, plan: PlanContext, uplift: float) -> Layer:
    def apply(drivers: Drivers) -> Drivers:
        factor = {
            key: 1 + uplift
            for key in drivers.sales
            if key[0] in event.launch_period
            and key[3] >= event.launch_period[key[0]]
            and plan.master.brand_of(key[1]).brand == event.brand
        }
        return _scale_sales(drivers, factor)

    return Layer(event.id, apply)


def _rd_acceleration(event: RdAcceleration, overrun: bool) -> Layer:
    """Pull-forward out of later months; forecasts also plan the overrun itself."""
    spend_key = (event.cost_center, event.gl_account)
    pulled = float(event.pulled_forward_lc) / len(event.pulled_from_periods)
    changes: dict[SpendKey, float] = {
        (*spend_key, p, event.wbs_element): -pulled for p in event.pulled_from_periods
    }
    if overrun:
        for p in event.periods:
            changes[*spend_key, p, event.wbs_element] = float(event.amount_lc) / len(event.periods)
    return Layer(event.id, lambda drivers: _add_spend(drivers, changes, event.id))


def _remove_budgeted(event_id: str, key: SpendKey, amount: float, year: int) -> Layer:
    """The cost was budgeted on ``key``; the event books it elsewhere or later."""

    def apply(drivers: Drivers) -> Drivers:
        if drivers.fiscal_year != year or key not in drivers.spend:
            return drivers
        return _add_spend(drivers, {key: -amount}, event_id)

    return Layer(event_id, apply)


def _transfer_price(event: TransferPriceChange, plan: PlanContext) -> Layer:
    def apply(drivers: Drivers) -> Drivers:
        if date(drivers.fiscal_year, 12, 31) < event.effective_from:
            return drivers
        prices = {
            key: price * (1 + float(event.change))
            if key[0] == event.buyer and plan.master.brand_of(key[1]).brand in event.brands
            else price
            for key, price in drivers.transfer_price.items()
        }
        return replace(drivers, transfer_price=prices)

    return Layer(event.id, apply)


def decoys(
    event: Decoys, plan: PlanContext, budget: Drivers, seed: int, last_closed: int
) -> list[Decoy]:
    """Small overspends in distinct company, period and P&L-line cells (SPEC §8.8 E12)."""
    master = plan.master
    taken = {cc for e in plan.scenario.events for cc in _cost_centers(e)}

    def usd(cost_center: str, amount: float) -> float:
        company_code = master.cost_centers[cost_center].company_code
        return amount * plan.to_usd(budget.version, company_code, budget.fiscal_year)

    sizes: dict[tuple[str, int, str], float] = defaultdict(float)
    for (cost_center, period), fte in budget.fte.items():
        if is_service(cost_center):
            continue
        center = master.cost_centers[cost_center]
        cell = (center.company_code, period, center.functional_area)
        sizes[cell] += usd(cost_center, personnel_cost(plan, cost_center, fte))
    candidates: dict[tuple[str, int, str], list[tuple[SpendKey, float]]] = defaultdict(list)
    for key, amount in sorted(budget.spend.items(), key=lambda item: str(item[0])):
        cost_center, _, period, wbs = key
        center = master.cost_centers[cost_center]
        cell = (center.company_code, period, center.functional_area)
        if not is_service(cost_center):
            sizes[cell] += usd(cost_center, amount)
        excluded = wbs or is_service(cost_center) or "-GBS-" in cost_center or cost_center in taken
        if not excluded and period <= last_closed and center.company_code in plan.companies:
            candidates[cell].append((key, usd(cost_center, amount)))

    rng = module_rng(seed, "events.decoys")
    low, high, limit = float(event.min_abs_usd), float(event.max_abs_usd), float(event.max_rel)
    ordered = sorted(candidates)
    year_cap = 0.95 * float(load_materiality().pnl_line.company_code.absolute)
    used: set[tuple[str, str]] = set()
    year_total: dict[tuple[str, str], float] = defaultdict(float)
    result = []
    for index in rng.permutation(len(ordered)):
        cell = ordered[index]
        line = (cell[0], cell[2])
        options = [(k, v) for k, v in candidates[cell] if k[:2] not in used]
        ceiling = min(0.97 * high, 0.9 * limit * sizes[cell], year_cap - year_total[line])
        if not options or ceiling < low:
            continue
        amount_usd = float(rng.uniform(low, ceiling))
        key, _ = max(options, key=lambda item: item[1])
        used.add(key[:2])
        year_total[line] += amount_usd
        rate = plan.to_usd(budget.version, cell[0], budget.fiscal_year)
        result.append(Decoy(key[0], key[1], key[2], amount_usd / rate, amount_usd))
        if len(result) == event.count:
            break
    return result


def _cost_centers(event: Event) -> set[str]:
    names = ("cost_center", "wrong_cost_center", "right_cost_center")
    return {getattr(event, name) for name in names if getattr(event, name, None)}


def _decoy_layer(
    event: Decoys, plan: PlanContext, budget: Drivers, seed: int, last_closed: int
) -> Layer:
    stable = event.stable_brand
    tolerance = float(event.stable_tolerance)
    planted: dict[SpendKey, float] = {
        (d.cost_center, d.gl_account, d.period, None): d.amount_lc
        for d in decoys(event, plan, budget, seed, last_closed)
    }

    def net(s: Sales, rates: dict[str, float]) -> float:
        return s.quantity * s.list_price * (1 - sum(rates.values()))

    def apply(drivers: Drivers) -> Drivers:
        if drivers.fiscal_year != budget.fiscal_year:
            return drivers
        factor: dict[tuple[str, str, str, int], float] = {}
        for company_code in plan.affiliates():
            keys = [
                k for k in drivers.sales
                if k[0] == company_code and k[3] <= last_closed
                and plan.master.brand_of(k[1]).brand == stable and k in budget.sales
            ]  # fmt: skip
            actual = sum(net(drivers.sales[k], drivers.sales[k].gtn_actual) for k in keys)
            planned = sum(net(budget.sales[k], budget.sales[k].gtn) for k in keys)
            if not planned:
                continue
            ratio = actual / planned
            target = min(max(ratio, 1 - tolerance / 2), 1 + tolerance / 2)
            factor |= dict.fromkeys(keys, target / ratio)
        return _add_spend(_scale_sales(drivers, factor), planted, event.id)

    return Layer(event.id, apply)


def layers(
    scenario: ScenarioConfig,
    plan: PlanContext,
    active: Iterable[str],
    budgets: Mapping[int, Drivers],
    seed: int,
    last_closed: int,
    version: str | None = None,
) -> list[Layer]:
    """Layers for actual drivers, or for a forecast ``version`` with the events it knows."""
    result = []
    events = known_events(scenario, active, version) if version else list(active)
    for event_id in events:
        event = scenario.event(event_id)
        match event:
            case LossOfExclusivity():
                result.append(_loss_of_exclusivity(event, plan))
            case LaunchOutperformance():
                uplift = event.forecast_signal[version] if version else event.volume_uplift  # type: ignore[index]
                result.append(_launch(event, plan, float(uplift)))
            case RdAcceleration():
                result.append(_rd_acceleration(event, overrun=version is not None))
            case Misposting():
                key = (event.right_cost_center, event.gl_account, event.posting_period, None)
                year = event.document.posting_date.year
                result.append(_remove_budgeted(event.id, key, float(event.amount_lc), year))
            case CutOffError():
                key = (event.cost_center, event.gl_account, event.incurred_period, None)
                year = event.document.posting_date.year
                result.append(_remove_budgeted(event.id, key, float(event.amount_lc), year))
            case TransferPriceChange():
                result.append(_transfer_price(event, plan))
            case Decoys():
                budget = budgets[event_year(scenario, event)]
                result.append(_decoy_layer(event, plan, budget, seed, last_closed))
    return result


# --- Event documents -------------------------------------------------------


def _document(
    ctx: LedgerContext,
    event_id: str,
    company_code: str,
    document: EventDocument,
    lines: list[Line],
    currency: str,
) -> Entry:
    return Entry(
        company_code=company_code,
        document_type=document.type,
        posting_date=document.posting_date,
        currency=currency,
        lines=tuple(lines),
        created_by=document.created_by,
        entry_ts=ctx.at(company_code, document.posting_date, 14),
        document_date=document.document_date or document.posting_date,
        header_text=document.header_text,
        event_id=event_id,
    )


def _month_end_document(
    ctx: LedgerContext, event_id: str, company_code: str, year: int, period: int,
    document_type: str, text: str, lines: list[Line], currency: str | None = None,
) -> Entry:  # fmt: skip
    day = ctx.working_days(year, period)[-1]
    document = EventDocument(type=document_type, posting_date=day,
                             created_by=NAMED_USERS[company_code], header_text=text)  # fmt: skip
    return _document(
        ctx, event_id, company_code, document, lines, currency or ctx.master.currency(company_code)
    )


def _write_off(ctx: LedgerContext, event: InventoryWriteOff) -> list[Entry]:
    amount = float(event.amount_lc)
    lines = [
        Line(event.gl_account, amount, cost_center=event.cost_center, material=event.material),
        Line(INVENTORY, -amount, material=event.material),
    ]
    return [_document(ctx, event.id, event.company_code, event.document, lines, event.currency)]


def _misposted_invoice(ctx: LedgerContext, event: Misposting) -> list[Entry]:
    amount = float(event.amount_lc)
    invoice = [
        Line(event.gl_account, amount, cost_center=event.wrong_cost_center, vendor=event.vendor),
        Line(AP, -amount, vendor=event.vendor),
    ]
    reclass = [
        Line(event.gl_account, amount, cost_center=event.right_cost_center, vendor=event.vendor),
        Line(event.gl_account, -amount, cost_center=event.wrong_cost_center, vendor=event.vendor),
    ]
    return [
        _document(ctx, event.id, event.company_code, event.document, invoice, event.currency),
        _document(
            ctx, event.id, event.company_code, event.reclass_document, reclass, event.currency
        ),
    ]


def _gtn_true_up(ctx: LedgerContext, event: GtnTrueUp, actuals: list[Drivers]) -> list[Entry]:
    """Spread over the brand's P01 to P(n-1) gross sales by customer and material."""
    drivers = next(d for d in actuals if d.fiscal_year == event.fiscal_year)
    gross: dict[tuple[str | None, str, str], float] = defaultdict(float)
    for period in range(1, event.period):
        for item in sales_items(ctx, drivers, event.company_code, period):
            if ctx.master.brand_of(item.material).brand == event.brand:
                gross[item.customer, item.material, item.channel] += item.gross
    total, amount = sum(gross.values()), float(event.amount_lc)
    lines = [
        Line(event.gl_account, amount * value / total, customer=customer, material=material,
             channel=channel, assignment=event.assignment)
        for (customer, material, channel), value in gross.items()
    ]  # fmt: skip
    lines.append(Line(event.offset_account, -amount, assignment=event.assignment))
    return [_document(ctx, event.id, event.company_code, event.document, lines, event.currency)]


def _rd_overrun(ctx: LedgerContext, event: RdAcceleration, year: int) -> list[Entry]:
    """Change orders for the accelerated enrollment, billed in local currency."""
    amount = float(event.amount_lc) / len(event.periods)
    result = []
    for period in event.periods:
        lines = [
            Line(event.gl_account, amount, cost_center=event.cost_center,
                 wbs_element=event.wbs_element, vendor=event.vendor),
            Line(AP, -amount, vendor=event.vendor),
        ]  # fmt: skip
        text = f"Change order {event.wbs_element}: accelerated enrollment"
        result.append(
            _month_end_document(ctx, event.id, event.company_code, year, period, "KR", text, lines)
        )
    return result


def _milestone(ctx: LedgerContext, event: CapitalisedMilestone) -> list[Entry]:
    amount = float(event.amount_tc)
    lines = [
        Line(event.debit_account, amount, vendor=event.vendor, assignment=event.asset),
        Line(event.credit_account, -amount, vendor=event.vendor, assignment=event.asset),
    ]
    return [_document(ctx, event.id, event.company_code, event.document, lines, event.currency)]


def _restructuring(ctx: LedgerContext, event: Restructuring, year: int) -> list[Entry]:
    result = []
    for provision in event.provisions:
        amount = float(provision.amount_lc)
        lines = [
            Line(event.gl_account, amount, profit_center=f"{provision.company_code}CORP"),
            Line(event.offset_account, -amount),
        ]
        text = "Sales force restructuring provision"
        result.append(
            _month_end_document(
                ctx,
                event.id,
                provision.company_code,
                year,
                event.period,
                "SA",
                text,
                lines,
                provision.currency,
            )
        )
    return result


def _cut_off(ctx: LedgerContext, event: CutOffError) -> list[Entry]:
    amount = float(event.amount_lc)
    lines = [
        Line(event.gl_account, amount, cost_center=event.cost_center, vendor=event.vendor),
        Line(AP, -amount, vendor=event.vendor),
    ]
    return [_document(ctx, event.id, event.company_code, event.document, lines, event.currency)]


def _divestment(ctx: LedgerContext, event: Divestment) -> list[Entry]:
    amount = float(event.amount_tc)
    lines = [
        Line(BANK, amount),
        Line(event.gl_account, -amount, profit_center=f"{event.company_code}CORP",
             text=f"Divestment of {event.divested_brand} rights"),
    ]  # fmt: skip
    text = f"Gain on divestment of {event.divested_brand}"
    return [_month_end_document(ctx, event.id, event.company_code, event.fiscal_year,
                                event.period, "SA", text, lines, event.currency)]  # fmt: skip


def entries(ctx: LedgerContext, actuals: list[Drivers], active: Iterable[str]) -> list[Entry]:
    years = {d.fiscal_year for d in actuals}
    result: list[Entry] = []
    for event_id in active:
        event = ctx.scenario.event(event_id)
        year = event_year(ctx.scenario, event)
        if year not in years:
            continue
        match event:
            case InventoryWriteOff():
                result += _write_off(ctx, event)
            case Misposting():
                result += _misposted_invoice(ctx, event)
            case GtnTrueUp():
                result += _gtn_true_up(ctx, event, actuals)
            case RdAcceleration():
                result += _rd_overrun(ctx, event, year)
            case CapitalisedMilestone():
                result += _milestone(ctx, event)
            case Restructuring():
                result += _restructuring(ctx, event, year)
            case CutOffError():
                result += _cut_off(ctx, event)
            case Divestment():
                result += _divestment(ctx, event)
    return [e for e in result if e.company_code in ctx.companies]
