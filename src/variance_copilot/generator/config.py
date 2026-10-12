"""Scenario and profile configuration (SPEC §8.1, §8.12, Appendix A)."""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from variance_copilot.config import load_yaml

DEFAULT_SCENARIO = Path("seeds/scenario/fy2025_fy2026.yaml")
DEFAULT_PROFILES = Path("seeds/profiles.yaml")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Month = Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
CompanyCode = Annotated[str, Field(pattern=r"^[A-Z]{2}\d{2}$")]
Currency = Literal["USD", "EUR", "CHF"]
PlanCategory = Literal["BUD25", "BUD26", "FC126", "FC226"]
Period = Annotated[int, Field(ge=1, le=16)]
Amount = Annotated[Decimal, Field(gt=0)]
Share = Annotated[Decimal, Field(ge=0, le=1)]
Change = Annotated[Decimal, Field(ge=-1, le=1)]
EventId = Literal[
    "E01", "E02", "E03", "E04a", "E04b", "E05", "E06", "E07", "E08", "E09", "E10", "E11", "E12"
]


# --- FX and calendar ------------------------------------------------------


class FxConfig(_Model):
    budget: dict[Literal["B25", "B26"], dict[Currency, Amount]]
    random_walk_sigma: dict[Currency, Annotated[Decimal, Field(gt=0)]]
    clip_to_budget: Share
    closing_noise_sigma: Annotated[Decimal, Field(gt=0)]
    override_noise: Share
    overrides: dict[Currency, dict[Month, Amount]] = {}
    forecast_rate_months: dict[Literal["F126", "F226"], Month]


class CalendarConfig(_Model):
    close_working_day: Annotated[int, Field(ge=1, le=10)]
    close_time: time
    special_period_close_month: Annotated[int, Field(ge=1, le=12)]
    time_zones: dict[CompanyCode, str]
    holidays: list[Annotated[str, Field(pattern=r"^\d{2}-\d{2}$")]] = []


# --- Calibration (SPEC §8.4, §8.5) ----------------------------------------


class BrandTarget(_Model):
    net_sales_musd: Amount
    by_market_musd: dict[Literal["US", "EU"], Amount]


class CalibrationConfig(_Model):
    tolerance: Share
    brands: dict[str, BrandTarget]
    group_net_sales_musd: Amount
    cost_structure: dict[Literal["COGS", "SELLING_MARKETING", "RD", "GA"], Share]
    fte: dict[CompanyCode, Amount]
    fte_tolerance: Share
    cost_per_fte_lc: dict[CompanyCode, Amount]

    @model_validator(mode="after")
    def _markets_add_up(self) -> Self:
        for brand, target in self.brands.items():
            if sum(target.by_market_musd.values()) != target.net_sales_musd:
                raise ValueError(f"{brand}: market targets do not add up to net_sales_musd")
        if sum(t.net_sales_musd for t in self.brands.values()) != self.group_net_sales_musd:
            raise ValueError("brand targets do not add up to group_net_sales_musd")
        return self


# --- Scenario events (SPEC §8.8, §8.10) -----------------------------------


class EventDocument(_Model):
    type: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    posting_date: date
    document_date: date | None = None
    created_by: str
    header_text: str


class _Event(_Model):
    id: EventId
    title: str
    known_by: list[PlanCategory] = []


class PriceChange(_Model):
    from_: date = Field(alias="from")
    change: Change


class LossOfExclusivity(_Event):
    type: Literal["LOSS_OF_EXCLUSIVITY"]
    brand: str
    company_code: CompanyCode
    actual_loe_date: date
    budget_loe_date: date
    volume_erosion_vs_baseline: dict[Month, Change]
    net_price_change: PriceChange


class FxShock(_Event):
    type: Literal["FX"]
    company_code: CompanyCode
    currency: Currency
    from_period: Period


class LaunchOutperformance(_Event):
    type: Literal["LAUNCH_OUTPERFORMANCE"]
    brand: str
    launch_period: dict[CompanyCode, Period]
    volume_uplift: Change
    forecast_signal: dict[PlanCategory, Change]


class RdAcceleration(_Event):
    type: Literal["RD_ACCELERATION"]
    company_code: CompanyCode
    wbs_element: str
    cost_center: str
    gl_account: str
    periods: list[Period]
    amount_lc: Amount
    pulled_forward_lc: Amount
    pulled_from_periods: list[Period]
    vendor: str


class CapitalisedMilestone(_Event):
    type: Literal["CAPITALISED_MILESTONE"]
    company_code: CompanyCode
    period: Period
    vendor: str
    asset: str
    debit_account: str
    credit_account: str
    amount_tc: Amount
    currency: Currency
    document: EventDocument


class InventoryWriteOff(_Event):
    type: Literal["INVENTORY_WRITE_OFF"]
    company_code: CompanyCode
    fiscal_year: int
    period: Period
    gl_account: str
    cost_center: str
    material: str
    amount_lc: Amount
    currency: Currency
    document: EventDocument


class Misposting(_Event):
    type: Literal["MISPOSTING"]
    company_code: CompanyCode
    brand: str
    vendor: str
    gl_account: str
    wrong_cost_center: str
    right_cost_center: str
    amount_lc: Amount
    currency: Currency
    posting_period: Period
    reclass_period: Period
    document: EventDocument
    reclass_document: EventDocument


class GtnTrueUp(_Event):
    type: Literal["GTN_TRUE_UP"]
    company_code: CompanyCode
    brand: str
    fiscal_year: int
    period: Period
    gl_account: str
    offset_account: str
    amount_lc: Amount
    currency: Currency
    assignment: str
    document: EventDocument


class Provision(_Model):
    company_code: CompanyCode
    amount_lc: Amount
    currency: Currency


class Restructuring(_Event):
    type: Literal["RESTRUCTURING"]
    period: Period
    gl_account: str
    offset_account: str
    provisions: list[Provision]


class CutOffError(_Event):
    type: Literal["CUT_OFF_ERROR"]
    company_code: CompanyCode
    gl_account: str
    cost_center: str
    vendor: str
    amount_lc: Amount
    currency: Currency
    incurred_period: Period
    document: EventDocument


class TransferPriceChange(_Event):
    type: Literal["TRANSFER_PRICE_CHANGE"]
    seller: CompanyCode
    buyer: CompanyCode
    brands: list[str]
    change: Change
    effective_from: date


class Divestment(_Event):
    type: Literal["DIVESTMENT"]
    company_code: CompanyCode
    fiscal_year: int
    period: Period
    gl_account: str
    divested_brand: str
    amount_tc: Amount
    currency: Currency


class Decoys(_Event):
    type: Literal["DECOYS"]
    count: Annotated[int, Field(ge=1)]
    min_abs_usd: Amount
    max_abs_usd: Amount
    max_rel: Share
    stable_brand: str
    stable_tolerance: Share


Event = Annotated[
    LossOfExclusivity | FxShock | LaunchOutperformance | RdAcceleration | CapitalisedMilestone
    | InventoryWriteOff | Misposting | GtnTrueUp | Restructuring | CutOffError
    | TransferPriceChange | Divestment | Decoys,
    Field(discriminator="type"),
]  # fmt: skip


class ScenarioConfig(_Model):
    """``seeds/scenario/fy2025_fy2026.yaml``."""

    scenario: str
    seed: int
    group_currency: Currency
    actuals_from: date
    as_of: date
    fx: FxConfig
    calendar: CalendarConfig
    calibration: CalibrationConfig
    events: list[Event]

    @model_validator(mode="after")
    def _unique_events(self) -> Self:
        ids = [event.id for event in self.events]
        if len(ids) != len(set(ids)):
            raise ValueError("event ids must be unique")
        return self

    def event(self, event_id: str) -> Event:
        return next(event for event in self.events if event.id == event_id)


# --- Profiles (SPEC §8.12) ------------------------------------------------


class PeriodRange(_Model):
    fiscal_year: int
    period: Period


class Profile(_Model):
    company_codes: list[CompanyCode]
    start: PeriodRange
    end: PeriodRange
    billing: Literal["WEEKLY", "MONTHLY"]
    events: list[EventId]
    journal_lines: tuple[int, int]

    @property
    def fiscal_years(self) -> list[int]:
        return list(range(self.start.fiscal_year, self.end.fiscal_year + 1))


class Profiles(_Model):
    """``seeds/profiles.yaml``."""

    profiles: dict[Literal["full", "dev", "ci"], Profile]


def load_scenario(path: Path = DEFAULT_SCENARIO) -> ScenarioConfig:
    return load_yaml(path, ScenarioConfig)


def load_profiles(path: Path = DEFAULT_PROFILES) -> Profiles:
    return load_yaml(path, Profiles)
