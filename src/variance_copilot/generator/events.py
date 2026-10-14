"""Scenario events (SPEC §8.8): driver layers and event documents."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date

from variance_copilot.generator.actual import Layer
from variance_copilot.generator.config import (
    EventDocument,
    FxShock,
    GtnTrueUp,
    InventoryWriteOff,
    LossOfExclusivity,
    Misposting,
    ScenarioConfig,
)
from variance_copilot.generator.curves import loe_erosion, month_index, month_index_of
from variance_copilot.generator.model import Drivers, Entry, Line, Sales
from variance_copilot.generator.plan import PlanContext, plan_loe
from variance_copilot.generator.streams import AP, INVENTORY, LedgerContext, sales_items

FX_EVENT = "E02"
IMPLEMENTED = frozenset({"E01", "E02", "E05", "E06", "E07"})
NET_SALES = ("GROSS_SALES", "GTN_DEDUCTION")
COSTS = ("COGS", "SELLING_MARKETING", "RESEARCH_DEVELOPMENT", "GENERAL_ADMIN", "OTHER_EXPENSE")


@dataclass(frozen=True)
class Measure:
    """The P&L slice an event's impact is measured on."""

    company_codes: tuple[str, ...]
    pnl_lines: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    brand: str | None = None
    third_party_only: bool = False


def active_events(
    scenario: ScenarioConfig, wanted: Iterable[str], exclude: Iterable[str] = ()
) -> list[str]:
    excluded = set(exclude)
    return [
        e.id
        for e in scenario.events
        if e.id in set(wanted) and e.id in IMPLEMENTED and e.id not in excluded
    ]


def measure(scenario: ScenarioConfig, event_id: str) -> Measure:
    event = scenario.event(event_id)
    match event:
        case LossOfExclusivity() | GtnTrueUp():
            return Measure(
                (event.company_code,),
                categories=NET_SALES,
                brand=event.brand,
                third_party_only=True,
            )
        case InventoryWriteOff():
            return Measure((event.company_code,), pnl_lines=("COGS",))
        case Misposting():
            return Measure((event.company_code,), pnl_lines=("RESEARCH_DEVELOPMENT",))
        case FxShock():
            return Measure((event.company_code,), pnl_lines=COSTS)
    raise NotImplementedError(event_id)


# --- Driver layers ---------------------------------------------------------


def _loss_of_exclusivity(event: LossOfExclusivity, plan: PlanContext) -> Layer:
    """Erosion against the pre-LoE baseline from the actual LoE date, plus a net price cut."""
    erosion = plan.revenue.brands[event.brand].erosion
    budget_loe = plan_loe(plan, event.brand, event.company_code)
    loe = month_index_of(budget_loe) if budget_loe else None

    def apply(drivers: Drivers) -> Drivers:
        sales = dict(drivers.sales)
        for key, s in drivers.sales.items():
            company_code, material, _, period = key
            if (
                company_code != event.company_code
                or plan.master.brand_of(material).brand != event.brand
            ):
                continue
            quantity, gtn, gtn_actual = s.quantity, dict(s.gtn), dict(s.gtn_actual)
            observed = event.volume_erosion_vs_baseline.get(f"{drivers.fiscal_year}-{period:02d}")
            if observed is not None:
                planned = loe_erosion(erosion, month_index(drivers.fiscal_year, period), loe)
                quantity = quantity / planned * (1 + float(observed))
            if date(drivers.fiscal_year, period, 1) >= event.net_price_change.from_:
                cut = -(1 - sum(s.gtn.values())) * float(event.net_price_change.change)
                gtn["COMMERCIAL"] += cut
                gtn_actual["COMMERCIAL"] += cut
            sales[key] = Sales(quantity, s.list_price, gtn, gtn_actual)
        return replace(drivers, sales=sales)

    return Layer(event.id, apply)


def _misposting(event: Misposting) -> Layer:
    """The campaign was budgeted on the marketing cost center; take it out of that spend."""
    year = event.document.posting_date.year
    key = (event.right_cost_center, event.gl_account, event.posting_period, None)

    def apply(drivers: Drivers) -> Drivers:
        if drivers.fiscal_year != year or key not in drivers.spend:
            return drivers
        remaining = drivers.spend[key] - float(event.amount_lc)
        if remaining < 0:
            raise ValueError(f"{event.id}: budgeted spend on {key} is below the campaign amount")
        return replace(drivers, spend={**drivers.spend, key: remaining})

    return Layer(event.id, apply)


def layers(scenario: ScenarioConfig, plan: PlanContext, active: Iterable[str]) -> list[Layer]:
    result = []
    for event_id in active:
        event = scenario.event(event_id)
        if isinstance(event, LossOfExclusivity):
            result.append(_loss_of_exclusivity(event, plan))
        elif isinstance(event, Misposting):
            result.append(_misposting(event))
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


def entries(ctx: LedgerContext, actuals: list[Drivers], active: Iterable[str]) -> list[Entry]:
    result: list[Entry] = []
    for event_id in active:
        event = ctx.scenario.event(event_id)
        if isinstance(event, InventoryWriteOff):
            result += _write_off(ctx, event)
        elif isinstance(event, Misposting):
            result += _misposted_invoice(ctx, event)
        elif isinstance(event, GtnTrueUp):
            result += _gtn_true_up(ctx, event, actuals)
    return result
