"""Planning assumptions and noise parameters (SPEC §8.4, §8.5, §8.9)."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Rate = Annotated[Decimal, Field(ge=-1, le=1)]
Share = Annotated[Decimal, Field(ge=0, le=1)]
Positive = Annotated[Decimal, Field(gt=0)]
NonNegative = Annotated[Decimal, Field(ge=0)]
Month = Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
Region = Literal["US", "EU"]
GtnComponent = Literal["COMMERCIAL", "GOVERNMENT", "RETURNS", "DISCOUNT"]
Stage = Literal["LAUNCH", "GROWTH", "MATURE", "DECLINE"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _sums_to_one(shares: Iterable[Decimal], what: str) -> None:
    total = sum(shares, Decimal(0))
    if total != 1:
        raise ValueError(f"{what} shares must add up to 1, got {total}")


# --- Revenue ---------------------------------------------------------------


class Lifecycle(_Model):
    stage: Stage
    growth: Rate = Decimal(0)
    decline: Share = Decimal(0)
    steepness: Positive = Decimal("0.35")
    midpoint_months_after_launch: int = 6


class Erosion(_Model):
    alpha: Share
    beta: Positive


class Sku(_Model):
    share: Share
    price_factor: Positive


class BrandAssumptions(_Model):
    lifecycle: Lifecycle
    launch: dict[str, Month] = {}
    launch_budget_lc: dict[str, Positive] = {}
    erosion: Erosion
    list_price: dict[str, Positive]
    gtn_rate: dict[Region, Share]
    cogs_ratio: Share
    amortisation_lc: NonNegative
    skus: dict[str, Sku]

    @model_validator(mode="after")
    def _sku_shares(self) -> Self:
        _sums_to_one((sku.share for sku in self.skus.values()), "SKU")
        return self


class Royalty(_Model):
    brand: str
    rate: Share
    company_code: str
    vendor: str


class RevenueAssumptions(_Model):
    calibration_year: int
    eu_split: dict[str, Share]
    seasonality: dict[str, Annotated[list[Positive], Field(min_length=12, max_length=12)]]
    annual_price_change: dict[str, Rate]
    channel_mix: dict[str, dict[str, dict[str, Share]]]
    channel_gtn_factor: dict[str, Positive]
    gtn_split: dict[Region, dict[GtnComponent, Share]]
    affiliate_margin: dict[str, Share]
    manufacturing_markup: Share
    freight_rate: dict[str, Share]
    royalty: Royalty
    brands: dict[str, BrandAssumptions]

    @model_validator(mode="after")
    def _shares(self) -> Self:
        _sums_to_one(self.eu_split.values(), "EU split")
        for region, split in self.gtn_split.items():
            _sums_to_one(split.values(), f"gtn_split.{region}")
        for company, by_modality in self.channel_mix.items():
            for modality, mix in by_modality.items():
                _sums_to_one(mix.values(), f"channel_mix.{company}.{modality}")
        return self


# --- Personnel and spend ---------------------------------------------------


class PersonnelAssumptions(_Model):
    fte: dict[str, Positive]
    annual_growth: dict[str, Rate]
    social_rate: dict[str, Share]
    bonus_rate: dict[str, Share]
    fte_per_head: Share
    payroll_day: Annotated[int, Field(ge=1, le=28)]


class ServiceCost(_Model):
    cost_center: str
    gl_account: str
    per_fte: Positive


class SellingMarketing(_Model):
    global_share: Share
    category_weight: dict[str, Share]
    accounts: dict[str, dict[str, Share]]


class ResearchDevelopment(_Model):
    trials: Share
    collaborations: Share
    medical: Share
    other: Share
    medical_accounts: dict[str, Share]
    other_accounts: dict[str, Share]
    collaboration_wbs: str
    collaboration_cost_center: str

    @model_validator(mode="after")
    def _split(self) -> Self:
        _sums_to_one(
            (self.trials, self.collaborations, self.medical, self.other), "research_development"
        )
        return self


class GeneralAdmin(_Model):
    accounts: dict[str, Share]


class TrialPhase(_Model):
    name: str
    duration: Share
    cost: Share


class Trial(_Model):
    start: Month
    months: Annotated[int, Field(ge=1)]
    weight: Positive
    vendor: str


class SpendAssumptions(_Model):
    service_costs: list[ServiceCost]
    depreciation_per_fte: dict[str, NonNegative]
    selling_marketing: SellingMarketing
    research_development: ResearchDevelopment
    general_admin: GeneralAdmin
    congress_months: list[Annotated[int, Field(ge=1, le=12)]]
    congress_weight: Positive
    trial_phases: list[TrialPhase]
    trials: dict[str, Trial]
    vendors: dict[str, dict[str, str]]

    def vendor(self, gl_account: str, company_code: str) -> str:
        by_company = self.vendors[gl_account]
        return by_company.get(company_code, by_company["default"])


class DriverAssumptions(_Model):
    revenue: RevenueAssumptions
    personnel: PersonnelAssumptions
    spend: SpendAssumptions


class NoiseAssumptions(_Model):
    volume_sigma: dict[Stage, Positive]
    net_price_sigma: Positive
    gtn_rate_pp: Positive
    spend_sigma: dict[str, Positive]
    invoice_lag_days: tuple[int, int]
    fte_sigma: Positive
    production_variance: Positive

    def spend(self, gl_account: str) -> Decimal:
        return self.spend_sigma.get(gl_account, Decimal("0.05"))
