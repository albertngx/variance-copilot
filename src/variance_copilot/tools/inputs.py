"""Common tool input types: scope, period, versions and bases (SPEC §10.3)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from variance_copilot.finance.materiality import ScopeLevel
from variance_copilot.finance.periods import (
    Period,
    PeriodSelection,
    PeriodView,
    RangeSelection,
    ViewSelection,
)

CompanyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}\d{2}$")]
Code = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9][A-Z0-9_-]{0,23}$")]
FunctionalArea = Annotated[str, StringConstraints(pattern=r"^F\d{3}$")]
GlAccount = Annotated[str, StringConstraints(pattern=r"^\d{8}$")]
DocumentNumber = Annotated[str, StringConstraints(pattern=r"^\d{10}$")]
DocumentType = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
MonthText = Annotated[str, StringConstraints(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
SearchText = Annotated[str, StringConstraints(min_length=2, max_length=100)]
FiscalYear = Annotated[int, Field(ge=2000, le=2100)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Version(StrEnum):
    """Version tokens; plan categories mirror ``fin.plan_version`` (SPEC §7.9)."""

    ACT = "ACT"
    PY = "PY"
    LATEST_FC = "LATEST_FC"
    BUD25 = "BUD25"
    BUD26 = "BUD26"
    FC126 = "FC126"
    FC226 = "FC226"


class CurrencyBasis(StrEnum):
    GC = "GC"
    LC = "LC"
    CC = "CC"


class Basis(StrEnum):
    IFRS = "IFRS"
    CORE = "CORE"


class Dimension(StrEnum):
    COMPANY_CODE = "company_code"
    PNL_LINE = "pnl_line"
    PNL_CATEGORY = "pnl_category"
    FUNCTIONAL_AREA = "functional_area"
    GL_ACCOUNT = "gl_account"
    SEGMENT = "segment"
    BRAND = "brand"
    PROFIT_CENTER = "profit_center"
    COST_CENTER = "cost_center"
    MATERIAL = "material"
    CHANNEL = "channel"
    WBS_ELEMENT = "wbs_element"
    PERIOD = "period"


class ScopeInput(InputModel):
    level: ScopeLevel = ScopeLevel.COMPANY_CODE
    company_codes: Annotated[tuple[CompanyCode, ...], Field(max_length=10)] = ()
    segment: Code | None = None
    brand: Code | None = None
    functional_area: FunctionalArea | None = None
    profit_center: Code | None = None
    cost_center: Code | None = None

    def filters(self) -> dict[str, str]:
        """The dimension filters that are set, by column name."""
        values = {
            "segment": self.segment,
            "brand": self.brand,
            "functional_area": self.functional_area,
            "profit_center": self.profit_center,
            "cost_center": self.cost_center,
        }
        return {name: value for name, value in values.items() if value is not None}


class PeriodViewInput(InputModel):
    fiscal_year: FiscalYear
    period: Annotated[int, Field(ge=1, le=12)]
    view: PeriodView = PeriodView.YTD

    def selection(self) -> PeriodSelection:
        return ViewSelection(self.fiscal_year, self.period, self.view)

    def label(self) -> str:
        return f"{self.view} P{self.period:02d} {self.fiscal_year}"


class PeriodRangeInput(InputModel):
    start: MonthText = Field(alias="from")
    end: MonthText = Field(alias="to")

    def selection(self) -> PeriodSelection:
        return RangeSelection(Period.parse(self.start), Period.parse(self.end))

    def label(self) -> str:
        return f"{self.start}..{self.end}"


PeriodInput = PeriodViewInput | PeriodRangeInput
