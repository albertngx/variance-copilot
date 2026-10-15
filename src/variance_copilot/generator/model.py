"""Shared types for the ledger generator: drivers, proto-documents and lookups."""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from variance_copilot.generator.fx import GROUP_CURRENCY, FxRate
from variance_copilot.generator.master import (
    Brand,
    CompanyCode,
    CostCenter,
    Customer,
    GlAccount,
    MarketExclusivity,
    MasterData,
    Material,
    ProfitCenter,
    WbsElement,
    rows_of,
)

ACTUAL = "ACT"
PLAN_RATE_TYPES = {"BUD25": "B25", "BUD26": "B26", "FC126": "F126", "FC226": "F226"}
GTN_ACCOUNTS = {"COMMERCIAL": "40100000", "GOVERNMENT": "40110000", "RETURNS": "40120000"}
DISCOUNT_ACCOUNT = "40130000"

SalesKey = tuple[str, str, str, int]  # company, material, channel, period
SpendKey = tuple[str, str, int, str | None]  # cost center, account, period, WBS element


@dataclass
class Sales:
    quantity: float
    list_price: float
    gtn: dict[str, float]
    gtn_actual: dict[str, float]


SalesPlan = dict[SalesKey, Sales]


@dataclass
class Drivers:
    """Business drivers of one version for one fiscal year (plan or actual)."""

    version: str
    fiscal_year: int
    periods: list[int]
    sales: SalesPlan
    transfer_price: dict[tuple[str, str], float]
    standard_cost: dict[str, float]
    fte: dict[tuple[str, int], float]
    spend: dict[SpendKey, float]
    depreciation: dict[tuple[str, int], float]
    production_variance: dict[tuple[str, int], float] = field(default_factory=dict)
    bonus_true_up: dict[str, float] = field(default_factory=dict)

    @property
    def is_actual(self) -> bool:
        return self.version == ACTUAL


@dataclass(frozen=True)
class Line:
    """One proto journal line; ``amount`` is in transaction currency, debit positive."""

    gl_account: str
    amount: float
    cost_center: str | None = None
    material: str | None = None
    channel: str | None = None
    customer: str | None = None
    vendor: str | None = None
    trading_partner: str | None = None
    wbs_element: str | None = None
    profit_center: str | None = None
    assignment: str | None = None
    quantity: float | None = None
    text: str | None = None


@dataclass(frozen=True)
class Entry:
    """A balanced proto-document before numbering and translation."""

    company_code: str
    document_type: str
    posting_date: date
    currency: str
    lines: tuple[Line, ...]
    created_by: str
    entry_ts: datetime
    document_date: date | None = None
    header_text: str | None = None
    accrual_key: str | None = None
    reverses: str | None = None
    event_id: str | None = None
    special_period: int | None = None

    @property
    def fiscal_year(self) -> int:
        return self.posting_date.year

    @property
    def period(self) -> int:
        return self.special_period or self.posting_date.month


class Rates:
    """USD per unit of currency, by rate type and validity date."""

    def __init__(self, rates: list[FxRate]) -> None:
        series: dict[tuple[str, str], list[tuple[date, float]]] = defaultdict(list)
        for rate in rates:
            series[rate.rate_type, rate.from_currency].append((rate.valid_from, float(rate.rate)))
        self._series = {key: sorted(values) for key, values in series.items()}

    def to_usd(self, rate_type: str, currency: str, on: date) -> float:
        if currency == GROUP_CURRENCY:
            return 1.0
        values = self._series[rate_type, currency]
        index = bisect.bisect_right(values, (on, float("inf"))) - 1
        if index < 0:
            raise KeyError(f"no {rate_type} rate for {currency} on {on}")
        return values[index][1]

    def convert(self, amount: float, rate_type: str, source: str, target: str, on: date) -> float:
        if source == target:
            return amount
        return amount * self.to_usd(rate_type, source, on) / self.to_usd(rate_type, target, on)


class Master:
    """Lookups over the master data seeds."""

    def __init__(self, data: MasterData) -> None:
        self.companies = {r.company_code: r for r in rows_of(data, CompanyCode)}
        self.cost_centers = {r.cost_center: r for r in rows_of(data, CostCenter)}
        self.profit_centers = {r.profit_center: r for r in rows_of(data, ProfitCenter)}
        self.accounts = {r.gl_account: r for r in rows_of(data, GlAccount)}
        self.materials = {r.material: r for r in rows_of(data, Material)}
        self.brands = {r.brand: r for r in rows_of(data, Brand)}
        self.customers = rows_of(data, Customer)
        self.loe = {(r.brand, r.country_code): r.loe_date for r in rows_of(data, MarketExclusivity)}
        self.wbs = {r.wbs_element: r for r in rows_of(data, WbsElement)}

    def currency(self, company_code: str) -> str:
        return self.companies[company_code].local_currency

    def country(self, company_code: str) -> str:
        return self.companies[company_code].country_code

    def cost_centers_of(self, company_code: str, *categories: str) -> list[CostCenter]:
        return [
            cc
            for cc in self.cost_centers.values()
            if cc.company_code == company_code and (not categories or cc.category in categories)
        ]

    def customers_of(self, company_code: str, channel: str) -> list[Customer]:
        country = self.country(company_code)
        return [c for c in self.customers if c.country_code == country and c.channel == channel]

    def channel_of(self, customer: str | None) -> str | None:
        return next((c.channel for c in self.customers if c.customer == customer), None)

    def brand_of(self, material: str) -> Brand:
        return self.brands[self.materials[material].brand]

    def profit_center_for(self, company_code: str, brand: str) -> str:
        if company_code == "IE10":
            return "IE10MFG"
        return company_code + self.brands[brand].therapeutic_area

    def derive(self, company_code: str, line: Line) -> tuple[str | None, str | None, str | None]:
        """Profit center, functional area (SPEC §7.4) and segment of a line."""
        account = self.accounts[line.gl_account]
        if line.cost_center:
            cost_center = self.cost_centers[line.cost_center]
            profit_center: str | None = cost_center.profit_center
            functional_area: str | None = cost_center.functional_area
        else:
            profit_center = line.profit_center
            if profit_center is None and line.material and account.account_type != "X":
                profit_center = self.profit_center_for(
                    company_code, self.brand_of(line.material).brand
                )
            functional_area = account.default_functional_area
        segment = self.profit_centers[profit_center].segment if profit_center else None
        return profit_center, functional_area, segment
