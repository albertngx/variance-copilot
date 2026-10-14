"""Proto-documents from drivers (SPEC §8.5, §8.7); the same rules feed plan lines and postings."""

from __future__ import annotations

import calendar as month_calendar
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from variance_copilot.generator.calendar import working_days
from variance_copilot.generator.config import ScenarioConfig
from variance_copilot.generator.model import (
    DISCOUNT_ACCOUNT,
    GTN_ACCOUNTS,
    PLAN_RATE_TYPES,
    Drivers,
    Entry,
    Line,
    Master,
    Rates,
)
from variance_copilot.generator.plan import (
    PlanContext,
    allocation_receivers,
    company_fte,
    is_service,
    salaries,
)
from variance_copilot.generator.rng import module_rng

PRINCIPAL, MANUFACTURER, GBS_HUB = "CH10", "IE10", "ES10"
NAMED_USERS = {
    "CH10": "lbrunner",
    "DE10": "mmueller",
    "ES10": "jgarcia",
    "IE10": "ofarrell",
    "US10": "kpatel",
}
PROVISION = {"COMMERCIAL": "21010000", "GOVERNMENT": "21010000", "RETURNS": "21020000"}
ALLOCATION_ACCOUNT = {"IT": "94000000", "FAC": "94010000"}
AR, AR_IC, INVENTORY, AP, AP_IC = "11000000", "11010000", "12000000", "20000000", "20010000"
ACCRUED, PAYROLL_LIABILITY = "21000000", "22000000"


@dataclass(frozen=True)
class LedgerContext:
    scenario: ScenarioConfig
    master: Master
    rates: Rates
    companies: list[str]
    billing: str
    seed: int

    @property
    def plan(self) -> PlanContext:
        return PlanContext(self.scenario, self.master, self.rates, self.companies)

    def affiliates(self) -> list[str]:
        return self.plan.affiliates()

    def working_days(self, year: int, month: int) -> list[date]:
        return working_days(year, month, self.scenario.calendar.holidays)

    def at(self, company_code: str, day: date, hour: int, minute: int = 0) -> datetime:
        zone = ZoneInfo(self.scenario.calendar.time_zones[company_code])
        return datetime.combine(day, time(hour, minute), zone)

    def to_usd(self, drivers: Drivers, currency: str, on: date) -> float:
        rate_type = "AVG" if drivers.is_actual else PLAN_RATE_TYPES[drivers.version]
        valid = on if drivers.is_actual else date(drivers.fiscal_year, 1, 1)
        return self.rates.to_usd(rate_type, currency, valid)


def _month_end(year: int, month: int) -> date:
    return date(year, month, month_calendar.monthrange(year, month)[1])


def _next_month(day: date) -> date:
    return _month_end(day.year, day.month) + timedelta(days=1)


def _entry(
    ctx: LedgerContext,
    company_code: str,
    document_type: str,
    posting_date: date,
    lines: Iterable[Line],
    created_by: str,
    entry_ts: datetime | None = None,
    currency: str | None = None,
    document_date: date | None = None,
    header_text: str | None = None,
    accrual_key: str | None = None,
    reverses: str | None = None,
) -> Entry:
    return Entry(
        company_code=company_code,
        document_type=document_type,
        posting_date=posting_date,
        currency=currency or ctx.master.currency(company_code),
        lines=tuple(line for line in lines if line.amount),
        created_by=created_by,
        entry_ts=entry_ts or ctx.at(company_code, posting_date, 9),
        document_date=document_date,
        header_text=header_text,
        accrual_key=accrual_key,
        reverses=reverses,
    )


def _offset(
    account: str,
    lines: Iterable[Line],
    customer: str | None = None,
    vendor: str | None = None,
    trading_partner: str | None = None,
) -> Line:
    """The balancing line of a document."""
    amount = -sum(line.amount for line in lines)
    return Line(account, amount, customer=customer, vendor=vendor, trading_partner=trading_partner)


# --- Sales -----------------------------------------------------------------


@dataclass(frozen=True)
class SalesItem:
    customer: str | None
    channel: str
    material: str
    quantity: float
    price: float
    gtn: dict[str, float]
    gtn_actual: dict[str, float]

    @property
    def gross(self) -> float:
        return self.quantity * self.price

    def line(self, gl_account: str, amount: float, quantity: float | None = None) -> Line:
        return Line(
            gl_account,
            amount,
            material=self.material,
            channel=self.channel,
            customer=self.customer,
            quantity=quantity,
        )


def sales_items(
    ctx: LedgerContext, drivers: Drivers, company_code: str, period: int
) -> list[SalesItem]:
    """Sales by customer (actuals) or by channel (plan) for one month."""
    items = []
    for (c, material, channel, p), s in drivers.sales.items():
        if (c, p) != (company_code, period):
            continue
        if not drivers.is_actual:
            items.append(
                SalesItem(None, channel, material, s.quantity, s.list_price, s.gtn, s.gtn_actual)
            )
            continue
        customers = ctx.master.customers_of(company_code, channel)
        weights = [1 / (k + 1) for k in range(len(customers))]
        for customer, weight in zip(customers, weights, strict=True):
            quantity = s.quantity * weight / sum(weights)
            items.append(
                SalesItem(
                    customer.customer,
                    channel,
                    material,
                    quantity,
                    s.list_price,
                    s.gtn,
                    s.gtn_actual,
                )
            )
    return items


def _billing_dates(ctx: LedgerContext, drivers: Drivers, year: int, month: int) -> list[date]:
    days = ctx.working_days(year, month)
    if drivers.is_actual and ctx.billing == "WEEKLY":
        return [day for day in days if day.weekday() == 4]
    return [days[min(10, len(days) - 1)]]


def sales_entries(
    ctx: LedgerContext, drivers: Drivers, company_code: str, period: int
) -> list[Entry]:
    year = drivers.fiscal_year
    items = sales_items(ctx, drivers, company_code, period)
    if not items:
        return []
    month_end = ctx.working_days(year, period)[-1]
    rates = "gtn_actual" if drivers.is_actual else "gtn"
    entries = []

    by_customer: dict[str | None, list[SalesItem]] = defaultdict(list)
    for item in items:
        by_customer[item.customer].append(item)
    dates = _billing_dates(ctx, drivers, year, period)
    for customer, customer_items in by_customer.items():
        for day in dates:
            share = 1 / len(dates)
            lines = []
            for item in customer_items:
                discount = getattr(item, rates)["DISCOUNT"]
                lines.append(item.line("40000000", -item.gross * share, item.quantity * share))
                lines.append(item.line(DISCOUNT_ACCOUNT, item.gross * share * discount))
            lines.append(_offset(AR, lines, customer=customer))
            entries.append(
                _entry(
                    ctx,
                    company_code,
                    "RV",
                    day,
                    lines,
                    "BATCH_BILLING",
                    ctx.at(company_code, day, 6),
                )
            )

    accrual = []
    for item in items:
        for component, account in GTN_ACCOUNTS.items():
            accrual.append(item.line(account, item.gross * item.gtn[component]))
    accrual += _provisions(accrual)
    entries.append(
        _entry(
            ctx,
            company_code,
            "ZR",
            month_end,
            accrual,
            "BATCH_BILLING",
            ctx.at(company_code, month_end, 20),
        )
    )

    if drivers.is_actual and period % 3 == 0:
        entries += _gtn_true_up(ctx, drivers, company_code, period, month_end)
    return entries


def _provisions(lines: list[Line]) -> list[Line]:
    by_account: dict[str, float] = defaultdict(float)
    accounts = {account: component for component, account in GTN_ACCOUNTS.items()}
    for line in lines:
        by_account[PROVISION[accounts[line.gl_account]]] -= line.amount
    return [Line(account, amount) for account, amount in by_account.items()]


def _gtn_true_up(
    ctx: LedgerContext, drivers: Drivers, company_code: str, period: int, day: date
) -> list[Entry]:
    """Quarterly true-up of the accrued rate to the estimated actual rate."""
    lines = []
    for p in (period - 2, period - 1, period):
        for item in sales_items(ctx, drivers, company_code, p):
            for component, account in GTN_ACCOUNTS.items():
                delta = item.gross * (item.gtn_actual[component] - item.gtn[component])
                lines.append(item.line(account, delta))
    lines += _provisions(lines)
    user = NAMED_USERS[company_code]
    text = f"GTN true-up Q{period // 3}"
    return [
        _entry(
            ctx,
            company_code,
            "ZR",
            day,
            lines,
            user,
            ctx.at(company_code, day, 16),
            header_text=text,
        )
    ]


# --- Intercompany goods, freight, royalty, amortisation --------------------


def goods_entries(ctx: LedgerContext, drivers: Drivers, period: int) -> list[Entry]:
    """IE10 -> CH10 -> affiliates chain; goods are resold in the month they arrive (I-07)."""
    if PRINCIPAL not in ctx.companies or MANUFACTURER not in ctx.companies:
        return []
    year = drivers.fiscal_year
    quantity: dict[tuple[str, str], float] = defaultdict(float)
    for (company_code, material, _, p), s in drivers.sales.items():
        if p == period:
            quantity[company_code, material] += s.quantity
    entries = []
    shipped: dict[str, float] = defaultdict(float)
    markup = 1 + float(ctx.scenario.drivers.revenue.manufacturing_markup)
    for buyer in ctx.affiliates():
        day = ctx.working_days(year, period)[-1]
        materials = [m for (c, m) in quantity if c == buyer]
        if not materials:
            continue
        currency = ctx.master.currency(buyer)
        tp_value = {m: quantity[buyer, m] * drivers.transfer_price[buyer, m] for m in materials}
        receipt = [Line(INVENTORY, v, material=m) for m, v in tp_value.items()]
        receipt.append(_offset(AP_IC, receipt, trading_partner=PRINCIPAL, vendor="IC-" + PRINCIPAL))
        issue = [
            Line("50060000", v, material=m, trading_partner=PRINCIPAL, quantity=quantity[buyer, m])
            for m, v in tp_value.items()
        ]
        issue.append(_offset(INVENTORY, issue))
        billing = [
            Line(
                "40010000",
                -v,
                material=m,
                trading_partner=buyer,
                customer="IC-" + buyer,
                quantity=quantity[buyer, m],
            )
            for m, v in tp_value.items()
        ]
        billing.append(_offset(AR_IC, billing, trading_partner=buyer, customer="IC-" + buyer))
        entries += [
            _entry(ctx, buyer, "WE", day, receipt, "BATCH_BILLING"),
            _entry(ctx, buyer, "WA", day, issue, "BATCH_BILLING"),
            _entry(ctx, PRINCIPAL, "RI", day, billing, "BATCH_BILLING", currency=currency),
        ]
        cost = {m: quantity[buyer, m] * markup * drivers.standard_cost[m] for m in materials}
        principal_issue = [
            Line(
                "50060000", v, material=m, trading_partner=MANUFACTURER, quantity=quantity[buyer, m]
            )
            for m, v in cost.items()
        ]
        principal_issue.append(_offset(INVENTORY, principal_issue))
        entries.append(
            _entry(ctx, PRINCIPAL, "WA", day, principal_issue, "BATCH_BILLING", currency="EUR")
        )
        for m in materials:
            shipped[m] += quantity[buyer, m]

    if not shipped:
        return entries
    day = ctx.working_days(year, period)[-1]
    cost = {m: q * markup * drivers.standard_cost[m] for m, q in shipped.items()}
    standard = {m: q * drivers.standard_cost[m] for m, q in shipped.items()}
    receipt = [Line(INVENTORY, v, material=m) for m, v in cost.items()]
    receipt.append(
        _offset(AP_IC, receipt, trading_partner=MANUFACTURER, vendor="IC-" + MANUFACTURER)
    )
    billing = [
        Line(
            "40010000",
            -v,
            material=m,
            trading_partner=PRINCIPAL,
            customer="IC-" + PRINCIPAL,
            quantity=shipped[m],
        )
        for m, v in cost.items()
    ]
    billing.append(_offset(AR_IC, billing, trading_partner=PRINCIPAL, customer="IC-" + PRINCIPAL))
    issue = [Line("50000000", v, material=m, quantity=shipped[m]) for m, v in standard.items()]
    issue.append(_offset(INVENTORY, issue))
    purchase = [Line(INVENTORY, v, material=m) for m, v in standard.items()]
    purchase.append(
        _offset(AP, purchase, vendor=ctx.scenario.drivers.spend.vendor(INVENTORY, MANUFACTURER))
    )
    entries += [
        _entry(ctx, PRINCIPAL, "WE", day, receipt, "BATCH_BILLING", currency="EUR"),
        _entry(ctx, MANUFACTURER, "RI", day, billing, "BATCH_BILLING"),
        _entry(ctx, MANUFACTURER, "WA", day, issue, "BATCH_BILLING"),
        _entry(ctx, MANUFACTURER, "KR", day, purchase, "INTERFACE_AP"),
    ]
    if drivers.is_actual:
        variance = [
            Line(
                "50010000",
                standard[m] * drivers.production_variance.get((m, period), 0.0),
                cost_center="IE10-MFG-API",
                material=m,
            )
            for m in standard
        ]
        variance.append(_offset(INVENTORY, variance))
        entries.append(
            _entry(
                ctx,
                MANUFACTURER,
                "SA",
                day,
                variance,
                NAMED_USERS[MANUFACTURER],
                header_text="Production variances",
            )
        )
    return entries


def freight_entries(ctx: LedgerContext, drivers: Drivers, period: int) -> list[Entry]:
    entries = []
    for company_code in ctx.affiliates():
        rate = float(ctx.scenario.drivers.revenue.freight_rate[company_code])
        gross: dict[str, float] = defaultdict(float)
        for (c, material, _, p), s in drivers.sales.items():
            if (c, p) == (company_code, period):
                gross[material] += s.quantity * s.list_price * rate
        if not gross:
            continue
        vendor = ctx.scenario.drivers.spend.vendor("50050000", company_code)
        lines = [Line("50050000", v, material=m, vendor=vendor) for m, v in gross.items()]
        lines.append(_offset(AP, lines, vendor=vendor))
        day = ctx.working_days(drivers.fiscal_year, period)[-1]
        entries.append(_entry(ctx, company_code, "KR", day, lines, "INTERFACE_AP"))
    return entries


def royalty_entries(ctx: LedgerContext, drivers: Drivers, period: int) -> list[Entry]:
    royalty = ctx.scenario.drivers.revenue.royalty
    if royalty.company_code not in ctx.companies:
        return []
    day = ctx.working_days(drivers.fiscal_year, period)[-1]
    rates = "gtn_actual" if drivers.is_actual else "gtn"
    net_usd: dict[str, float] = defaultdict(float)
    for (company_code, material, _, p), s in drivers.sales.items():
        if p == period and ctx.master.brand_of(material).brand == royalty.brand:
            net = s.quantity * s.list_price * (1 - sum(getattr(s, rates).values()))
            net_usd[material] += net * ctx.to_usd(drivers, ctx.master.currency(company_code), day)
    lines = [
        Line("50030000", v * float(royalty.rate), material=m, vendor=royalty.vendor)
        for m, v in net_usd.items()
    ]
    lines.append(_offset(ACCRUED, lines, vendor=royalty.vendor))
    user = NAMED_USERS[royalty.company_code]
    return [
        _entry(
            ctx,
            royalty.company_code,
            "SA",
            day,
            lines,
            user,
            currency="USD",
            header_text=f"{royalty.brand} royalty accrual",
        )
    ]


def amortisation_entries(ctx: LedgerContext, drivers: Drivers, period: int) -> list[Entry]:
    if PRINCIPAL not in ctx.companies:
        return []
    lines = [
        Line(
            "50040000",
            float(a.amortisation_lc) / 12,
            profit_center=ctx.master.profit_center_for(PRINCIPAL, brand),
        )
        for brand, a in ctx.scenario.drivers.revenue.brands.items()
        if a.amortisation_lc
    ]
    lines.append(_offset("13010000", lines))
    day = ctx.working_days(drivers.fiscal_year, period)[-1]
    return [
        _entry(
            ctx,
            PRINCIPAL,
            "SA",
            day,
            lines,
            NAMED_USERS[PRINCIPAL],
            header_text="Amortisation of marketed products",
        )
    ]


# --- Personnel, spend, depreciation ---------------------------------------


def _payroll_day(ctx: LedgerContext, year: int, month: int) -> date:
    target = date(year, month, ctx.scenario.drivers.personnel.payroll_day)
    return max(day for day in ctx.working_days(year, month) if day <= target)


def payroll_entries(ctx: LedgerContext, drivers: Drivers, period: int) -> list[Entry]:
    personnel = ctx.scenario.drivers.personnel
    by_company: dict[str, list[Line]] = defaultdict(list)
    for (cost_center, p), fte in drivers.fte.items():
        if p != period:
            continue
        company_code = ctx.master.cost_centers[cost_center].company_code
        base = salaries(ctx.plan, cost_center, fte)
        by_company[company_code] += [
            Line("60000000", base, cost_center=cost_center),
            Line(
                "60010000",
                base * float(personnel.social_rate[company_code]),
                cost_center=cost_center,
            ),
            Line(
                "60020000",
                base * float(personnel.bonus_rate[company_code]),
                cost_center=cost_center,
            ),
        ]
    entries = []
    for company_code, lines in by_company.items():
        day = _payroll_day(ctx, drivers.fiscal_year, period)
        lines.append(_offset(PAYROLL_LIABILITY, lines))
        entries.append(
            _entry(
                ctx,
                company_code,
                "SA",
                day,
                lines,
                "BATCH_PAYROLL",
                ctx.at(company_code, day, 7),
                header_text="Payroll",
            )
        )
        if drivers.is_actual and period == 12:
            entries.append(_bonus_true_up(ctx, drivers, company_code))
    return entries


def _bonus_true_up(ctx: LedgerContext, drivers: Drivers, company_code: str) -> Entry:
    rate = float(ctx.scenario.drivers.personnel.bonus_rate[company_code])
    annual: dict[str, float] = defaultdict(float)
    for (cost_center, _), fte in drivers.fte.items():
        if ctx.master.cost_centers[cost_center].company_code == company_code:
            annual[cost_center] += salaries(ctx.plan, cost_center, fte) * rate
    factor = drivers.bonus_true_up.get(company_code, 0.0)
    lines = [Line("60020000", v * factor, cost_center=cc) for cc, v in annual.items()]
    lines.append(_offset(ACCRUED, lines))
    day = ctx.working_days(drivers.fiscal_year, 12)[-1]
    return _entry(
        ctx, company_code, "SA", day, lines, NAMED_USERS[company_code], header_text="Bonus true-up"
    )


def _vendor(ctx: LedgerContext, cost_center: str, gl_account: str, wbs: str | None) -> str:
    spend = ctx.scenario.drivers.spend
    if wbs in spend.trials:
        return spend.trials[wbs].vendor
    return spend.vendor(gl_account, ctx.master.cost_centers[cost_center].company_code)


def _invoice_currency(
    ctx: LedgerContext, company_code: str, year: int, line: Line
) -> tuple[str, Line]:
    """Vendors with foreign-currency contracts bill the budgeted amount at the budget rate."""
    local = ctx.master.currency(company_code)
    currency = ctx.scenario.drivers.spend.invoice_currency.get(line.vendor or "", local)
    if currency == local:
        return local, line
    rate_type, valid = f"B{year % 100:02d}", date(year, 1, 1)
    rate = ctx.rates.to_usd(rate_type, local, valid) / ctx.rates.to_usd(rate_type, currency, valid)
    return currency, replace(line, amount=line.amount * rate)


def spend_entries(ctx: LedgerContext, drivers: Drivers, period: int) -> list[Entry]:
    """Vendor invoices; services not invoiced by month end are accrued and reversed (SPEC §8.7)."""
    year = drivers.fiscal_year
    keys = sorted((k for k in drivers.spend if k[2] == period), key=str)
    month_end = ctx.working_days(year, period)[-1]
    by_company: dict[str, list[Line]] = defaultdict(list)
    for key in keys:
        cost_center, gl_account, _, wbs = key
        by_company[ctx.master.cost_centers[cost_center].company_code].append(
            Line(
                gl_account,
                drivers.spend[key],
                cost_center=cost_center,
                wbs_element=wbs,
                vendor=_vendor(ctx, cost_center, gl_account, wbs),
            )
        )
    entries = []
    if not drivers.is_actual:
        for company_code, lines in by_company.items():
            entries.append(
                _entry(
                    ctx, company_code, "KR", month_end, [*lines, _offset(AP, lines)], "INTERFACE_AP"
                )
            )
        return entries

    lag = module_rng(ctx.seed, f"postings.invoice_lag.{year}.{period}")
    low, high = ctx.scenario.noise.invoice_lag_days
    days_in_month = month_calendar.monthrange(year, period)[1]
    invoices = ctx.scenario.drivers.spend.invoices_per_month
    for company_code, lines in by_company.items():
        accrued = []
        parts = [
            replace(line, amount=line.amount / count)
            for line in lines
            for count in [invoices.get(line.gl_account, 1)]
            for _ in range(count)
        ]
        for line in parts:
            service = date(year, period, int(lag.integers(1, days_in_month + 1)))
            invoice = service + timedelta(days=int(lag.integers(low, high + 1)))
            currency, billed = _invoice_currency(ctx, company_code, year, line)
            invoice_lines = [billed, _offset(AP, [billed], vendor=line.vendor)]
            entries.append(
                _entry(
                    ctx,
                    company_code,
                    "KR",
                    invoice,
                    invoice_lines,
                    "INTERFACE_AP",
                    currency=currency,
                    document_date=service,
                )
            )
            if invoice.month != period:
                accrued.append(line)
        if accrued:
            accrual_key = f"ACR-{company_code}-{year}-{period:02d}"
            lines = [*accrued, _offset(ACCRUED, accrued)]
            user = NAMED_USERS[company_code]
            text = f"Accrued services {year}-{period:02d}"
            entries.append(
                _entry(
                    ctx,
                    company_code,
                    "SA",
                    month_end,
                    lines,
                    user,
                    ctx.at(company_code, month_end, 15),
                    header_text=text,
                    accrual_key=accrual_key,
                )
            )
            reversal = [
                Line(
                    line.gl_account,
                    -line.amount,
                    cost_center=line.cost_center,
                    wbs_element=line.wbs_element,
                    vendor=line.vendor,
                )
                for line in lines
            ]
            day = _next_month(month_end)
            entries.append(
                _entry(
                    ctx,
                    company_code,
                    "SA",
                    day,
                    reversal,
                    user,
                    ctx.at(company_code, day, 6),
                    header_text=f"Reversal: {text}",
                    reverses=accrual_key,
                )
            )
    return entries


def _close_run(
    ctx: LedgerContext, company_code: str, year: int, period: int
) -> tuple[date, datetime]:
    """Period-end posting date and the entry time on working day 2 of the close."""
    period_end = _month_end(year, period)
    following = _next_month(period_end)
    run_day = ctx.working_days(following.year, following.month)[1]
    return period_end, ctx.at(company_code, run_day, 10)


def depreciation_entries(ctx: LedgerContext, drivers: Drivers, period: int) -> list[Entry]:
    by_company: dict[str, list[Line]] = defaultdict(list)
    for (cost_center, p), amount in drivers.depreciation.items():
        if p == period:
            company_code = ctx.master.cost_centers[cost_center].company_code
            by_company[company_code].append(Line("61060000", amount, cost_center=cost_center))
    entries = []
    for company_code, lines in by_company.items():
        day, entry_ts = _close_run(ctx, company_code, drivers.fiscal_year, period)
        entries.append(
            _entry(
                ctx,
                company_code,
                "AF",
                day,
                [*lines, _offset("14010000", lines)],
                "BATCH_ALLOC",
                entry_ts,
            )
        )
    return entries


# --- Close runs computed from posted entries -------------------------------


def _cost_center_totals(entries: Iterable[Entry], year: int, period: int) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for entry in entries:
        if (entry.fiscal_year, entry.period) == (year, period):
            for line in entry.lines:
                if line.cost_center:
                    totals[line.cost_center] += line.amount
    return totals


def allocation_entries(
    ctx: LedgerContext, drivers: Drivers, entries: list[Entry], period: int
) -> list[Entry]:
    """Assess service cost centers to receivers in the same company by FTE (SPEC §8.5)."""
    totals = _cost_center_totals(entries, drivers.fiscal_year, period)
    fte = {key: value for key, value in drivers.fte.items() if key[1] == period}
    result = []
    for company_code in ctx.companies:
        lines = []
        receivers = allocation_receivers(ctx.master, company_code, (cc for cc, _ in fte))
        total_fte = sum(fte[r, period] for r in receivers)
        for sender in [
            cc.cost_center
            for cc in ctx.master.cost_centers_of(company_code)
            if is_service(cc.cost_center)
        ]:
            amount = totals.get(sender, 0.0)
            if not amount or not receivers:
                continue
            account = ALLOCATION_ACCOUNT[sender.rsplit("-", 1)[1]]
            lines.append(Line(account, -amount, cost_center=sender))
            lines += [
                Line(account, amount * fte[r, period] / total_fte, cost_center=r) for r in receivers
            ]
        if lines:
            day, entry_ts = _close_run(ctx, company_code, drivers.fiscal_year, period)
            result.append(
                _entry(
                    ctx,
                    company_code,
                    "AL",
                    day,
                    lines,
                    "BATCH_ALLOC",
                    entry_ts,
                    header_text="Service cost assessment",
                )
            )
    return result


def gbs_entries(
    ctx: LedgerContext, drivers: Drivers, entries: list[Entry], period: int
) -> list[Entry]:
    """ES10 shared services charged to the other entities at cost plus 5% by FTE."""
    if GBS_HUB not in ctx.companies:
        return []
    totals = _cost_center_totals(entries, drivers.fiscal_year, period)
    cost = sum(v for cc, v in totals.items() if cc.startswith(f"{GBS_HUB}-GBS-"))
    receivers = [c for c in ctx.companies if c != GBS_HUB]
    fte = company_fte(ctx.master, {k: v for k, v in drivers.fte.items() if k[1] == period})
    total_fte = sum(fte[c, period] for c in receivers)
    if not cost or not total_fte:
        return []
    charge = cost * 1.05
    result = []
    for receiver in receivers:
        amount = charge * fte[receiver, period] / total_fte
        day, entry_ts = _close_run(ctx, receiver, drivers.fiscal_year, period)
        billing = [
            Line(
                "40600000",
                -amount,
                trading_partner=receiver,
                customer="IC-" + receiver,
                profit_center="ES10GBS",
            )
        ]
        billing.append(_offset(AR_IC, billing, trading_partner=receiver, customer="IC-" + receiver))
        admin = ctx.master.cost_centers_of(receiver, "ADMINISTRATION")[0].cost_center
        vendor = "IC-" + GBS_HUB
        expense = [
            Line("61090000", amount, cost_center=admin, trading_partner=GBS_HUB, vendor=vendor)
        ]
        expense.append(_offset(AP_IC, expense, trading_partner=GBS_HUB, vendor=vendor))
        result += [
            _entry(
                ctx,
                GBS_HUB,
                "RI",
                day,
                billing,
                "BATCH_BILLING",
                entry_ts,
                header_text="GBS service fee",
            ),
            _entry(
                ctx,
                receiver,
                "KR",
                day,
                expense,
                "INTERFACE_AP",
                entry_ts,
                currency="EUR",
                header_text="GBS service fee",
            ),
        ]
    return result


# --- All streams -----------------------------------------------------------


def base_entries(ctx: LedgerContext, drivers: Drivers) -> Iterator[Entry]:
    for period in drivers.periods:
        for company_code in ctx.affiliates():
            yield from sales_entries(ctx, drivers, company_code, period)
        yield from goods_entries(ctx, drivers, period)
        yield from freight_entries(ctx, drivers, period)
        yield from royalty_entries(ctx, drivers, period)
        yield from amortisation_entries(ctx, drivers, period)
        yield from payroll_entries(ctx, drivers, period)
        yield from spend_entries(ctx, drivers, period)
        yield from depreciation_entries(ctx, drivers, period)


def close_entries(ctx: LedgerContext, drivers: Drivers, entries: list[Entry]) -> list[Entry]:
    result = []
    for period in drivers.periods:
        result += allocation_entries(ctx, drivers, entries, period)
        result += gbs_entries(ctx, drivers, entries, period)
    return result
