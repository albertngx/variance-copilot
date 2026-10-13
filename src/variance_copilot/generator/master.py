"""Master data seeds (SPEC §7.3-§7.7): ``seeds/master/*.yaml`` to ``md.*``."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator
from sqlalchemy import Connection

from variance_copilot.config import ConfigError
from variance_copilot.generator.db import upsert

DEFAULT_SEEDS_DIR = Path("seeds/master")
CONTROLLING_AREA = "A000"
CHART_OF_ACCOUNTS = "AUR1"
VALID_FROM = date(2020, 1, 1)
VALID_TO = date(9999, 12, 31)


EntityRole = Literal["PRINCIPAL", "SALES_AFFILIATE", "MANUFACTURING", "SHARED_SERVICES"]
PnlLine = Literal[
    "REVENUE", "COGS", "SELLING_MARKETING", "RESEARCH_DEVELOPMENT", "GENERAL_ADMIN",
    "OTHER_INCOME", "OTHER_EXPENSE",
]  # fmt: skip
CostCenterCategory = Literal[
    "PRODUCTION", "QUALITY", "SALES", "MARKETING", "MEDICAL", "RESEARCH", "DEVELOPMENT",
    "ADMINISTRATION", "SERVICE",
]  # fmt: skip
PnlCategory = Literal[
    "GROSS_SALES", "GTN_DEDUCTION", "OTHER_REVENUE", "COGS", "OPEX", "ALLOCATION",
    "OTHER_OPERATING",
]  # fmt: skip
CoreAdjustment = Literal["AMORTIZATION", "IMPAIRMENT", "RESTRUCTURING", "LEGAL", "DIVESTMENT"]
Channel = Literal["WHOLESALER", "SPECIALTY_PHARMACY", "HOSPITAL", "PUBLIC_TENDER", "INTERCOMPANY"]
VendorCategory = Literal[
    "CRO", "MARKETING_AGENCY", "EVENTS", "CONSULTING", "IT", "FACILITIES", "LOGISTICS",
    "LICENSOR", "RAW_MATERIALS", "INTERCOMPANY", "OTHER",
]  # fmt: skip


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, coerce_numbers_to_str=True)


class Currency(_Row):
    currency_code: str
    name: str
    decimals: int = 2


class ControllingArea(_Row):
    controlling_area: str
    name: str
    currency_code: str


class CompanyCode(_Row):
    company_code: str
    legal_name: str
    city: str
    country_code: str
    local_currency: str
    chart_of_accounts: str
    controlling_area: str
    trading_partner: str
    entity_roles: list[EntityRole]


class Segment(_Row):
    segment: str
    name: str


class FunctionalArea(_Row):
    functional_area: str
    name: str
    pnl_line: PnlLine
    sort_order: int


class ProfitCenter(_Row):
    profit_center: str
    name: str
    segment: str
    therapeutic_area: str | None
    controlling_area: str = CONTROLLING_AREA
    valid_from: date = VALID_FROM
    valid_to: date = VALID_TO


class CostCenter(_Row):
    cost_center: str
    category: CostCenterCategory
    functional_area: str
    profit_center: str
    name: str
    company_code: str = ""
    controlling_area: str = CONTROLLING_AREA
    person_responsible: str | None = None
    valid_from: date = VALID_FROM
    valid_to: date = VALID_TO

    @model_validator(mode="after")
    def _company_from_key(self) -> CostCenter:
        if not self.company_code:
            object.__setattr__(self, "company_code", self.cost_center[:4])
        if not self.cost_center.startswith(self.company_code):
            raise ValueError(f"{self.cost_center} does not start with {self.company_code}")
        return self


class GlAccount(_Row):
    gl_account: str
    name: str
    account_type: Literal["X", "P", "S"]
    pnl_category: PnlCategory | None
    default_functional_area: str | None
    normal_balance: Literal["D", "C"]
    core_adjustment: CoreAdjustment | None
    is_intercompany: bool
    chart_of_accounts: str = CHART_OF_ACCOUNTS

    @model_validator(mode="after")
    def _balance_sheet_has_no_category(self) -> GlAccount:
        if (self.account_type == "X") != (self.pnl_category is None):
            raise ValueError("balance sheet accounts (type X) and only they have no P&L category")
        return self


class Brand(_Row):
    brand: str
    therapeutic_area: str
    modality: Literal["SMALL_MOLECULE", "BIOLOGIC", "INHALED"]
    lifecycle_stage: Literal["LAUNCH", "GROWTH", "MATURE", "DECLINE"]


class MarketExclusivity(_Row):
    brand: str
    country_code: str
    loe_date: date
    basis: str


class Material(_Row):
    material: str
    brand: str
    description: str
    strength: str
    pack_size: int
    base_unit: str = "PK"


class Customer(_Row):
    customer: str
    name: str
    country_code: str
    channel: Channel
    trading_partner: str | None


class Vendor(_Row):
    vendor: str
    name: str
    country_code: str
    category: VendorCategory
    trading_partner: str | None


class WbsElement(_Row):
    wbs_element: str
    description: str
    company_code: str
    cost_center: str
    brand: str | None
    project_type: Literal["CLINICAL_TRIAL", "RESEARCH", "LIFECYCLE", "OTHER"]
    phase: str | None


# Load order follows the foreign keys.
TABLES: dict[str, tuple[type[_Row], tuple[str, ...]]] = {
    "currency": (Currency, ("currency_code",)),
    "controlling_area": (ControllingArea, ("controlling_area",)),
    "company_code": (CompanyCode, ("company_code",)),
    "segment": (Segment, ("segment",)),
    "functional_area": (FunctionalArea, ("functional_area",)),
    "profit_center": (ProfitCenter, ("profit_center",)),
    "cost_center": (CostCenter, ("cost_center",)),
    "gl_account": (GlAccount, ("chart_of_accounts", "gl_account")),
    "brand": (Brand, ("brand",)),
    "market_exclusivity": (MarketExclusivity, ("brand", "country_code")),
    "material": (Material, ("material",)),
    "customer": (Customer, ("customer",)),
    "vendor": (Vendor, ("vendor",)),
    "wbs_element": (WbsElement, ("wbs_element",)),
}

MasterData = dict[str, list[_Row]]


def _parse_row(model: type[_Row], raw: Any) -> _Row:
    if isinstance(raw, list):
        raw = dict(zip(model.model_fields, raw, strict=False))
    return model.model_validate(raw)


def read_master_data(seeds_dir: Path = DEFAULT_SEEDS_DIR) -> MasterData:
    """Validated rows per ``md`` table from every YAML file in ``seeds_dir``."""
    data: MasterData = {table: [] for table in TABLES}
    for path in sorted(seeds_dir.glob("*.yaml")):
        content: dict[str, list[Any]] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for table, rows in content.items():
            if table not in TABLES:
                raise ConfigError(f"{path}: unknown master data table {table!r}")
            model = TABLES[table][0]
            for index, raw in enumerate(rows):
                try:
                    data[table].append(_parse_row(model, raw))
                except ValidationError as exc:
                    raise ConfigError(f"{path}: {table}[{index}]:\n{exc}") from exc
    for table, rows in data.items():
        keys = TABLES[table][1]
        seen = [tuple(getattr(row, key) for key in keys) for row in rows]
        duplicates = sorted({key for key in seen if seen.count(key) > 1})
        if duplicates:
            raise ConfigError(f"duplicate {table} keys: {duplicates}")
    return data


def write_master_data(conn: Connection, data: MasterData) -> dict[str, int]:
    """Upsert every table in FK order; returns row counts."""
    counts = {}
    for table, (_, keys) in TABLES.items():
        rows = [row.model_dump() for row in data[table]]
        upsert(conn, f"md.{table}", rows, keys)
        counts[table] = len(rows)
    return counts


def rows_of[R: _Row](data: MasterData, model: type[R]) -> list[R]:
    table = next(name for name, (table_model, _) in TABLES.items() if table_model is model)
    return [row for row in data[table] if isinstance(row, model)]
