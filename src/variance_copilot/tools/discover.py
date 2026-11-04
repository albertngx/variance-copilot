"""Discovery tools: dimension members, period status and FX rates (SPEC §10.3)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from variance_copilot.finance.codes import FinanceError, WarningCode
from variance_copilot.finance.materiality import ScopeLevel
from variance_copilot.finance.periods import PeriodState, resolve_periods
from variance_copilot.finance.reference import load_fx_rates, load_period_statuses
from variance_copilot.tools.context import MAX_ROWS, ToolContext
from variance_copilot.tools.facts import FactKind, FactRef
from variance_copilot.tools.inputs import (
    CompanyCode,
    FiscalYear,
    InputModel,
    PeriodInput,
    SearchText,
)
from variance_copilot.tools.queries import members
from variance_copilot.tools.results import EffectiveScope, ToolResult
from variance_copilot.tools.scope import visible_companies


class MemberDimension(StrEnum):
    COMPANY_CODE = "company_code"
    SEGMENT = "segment"
    FUNCTIONAL_AREA = "functional_area"
    PROFIT_CENTER = "profit_center"
    COST_CENTER = "cost_center"
    GL_ACCOUNT = "gl_account"
    BRAND = "brand"
    MATERIAL = "material"
    WBS_ELEMENT = "wbs_element"


class ListDimensionMembersInput(InputModel):
    dimension: MemberDimension
    filter: SearchText | None = None
    limit: Annotated[int, Field(ge=1, le=MAX_ROWS)] = 50


class DimensionMember(BaseModel):
    code: str
    name: str | None
    attributes: dict[str, str]


class ListDimensionMembersResult(ToolResult):
    dimension: MemberDimension
    members: list[DimensionMember]


def list_dimension_members(
    params: ListDimensionMembersInput, ctx: ToolContext
) -> ListDimensionMembersResult:
    company_codes = sorted(ctx.principal.company_scope)
    found = members(
        ctx.conn, params.dimension.value, company_codes, params.filter, params.limit + 1
    )
    warnings = [WarningCode.TRUNCATED] if len(found) > params.limit else []
    warnings += [] if found else [WarningCode.NO_DATA]
    return ListDimensionMembersResult(
        warnings=warnings,
        dimension=params.dimension,
        members=[DimensionMember(code=m.code, name=m.name, attributes=m.attributes) for m in found][
            : params.limit
        ],
    )


class GetPeriodStatusInput(InputModel):
    company_codes: Annotated[tuple[CompanyCode, ...], Field(max_length=10)] = ()
    fiscal_year: FiscalYear
    period: Annotated[int, Field(ge=1, le=16)] | None = None


class PeriodStatusRow(BaseModel):
    company_code: str
    period: str
    status: PeriodState
    closed_at: datetime | None


class GetPeriodStatusResult(ToolResult):
    effective_scope: EffectiveScope
    rows: list[PeriodStatusRow]


def get_period_status(params: GetPeriodStatusInput, ctx: ToolContext) -> GetPeriodStatusResult:
    company_codes, warnings = visible_companies(params.company_codes, ctx.principal)
    rows = [
        PeriodStatusRow(
            company_code=s.company_code,
            period=str(s.period),
            status=s.state,
            closed_at=s.closed_at,
        )
        for s in load_period_statuses(ctx.conn, company_codes)
        if s.period.fiscal_year == params.fiscal_year
        and (params.period is None or s.period.period == params.period)
    ]
    return GetPeriodStatusResult(
        warnings=[*warnings, *([] if rows else [WarningCode.NO_DATA])],
        effective_scope=EffectiveScope(
            level=ScopeLevel.COMPANY_CODE, company_codes=list(company_codes)
        ),
        rows=rows,
    )


class RateType(StrEnum):
    AVG = "AVG"
    CLO = "CLO"
    B25 = "B25"
    B26 = "B26"
    F126 = "F126"
    F226 = "F226"


class GetFxRatesInput(InputModel):
    currencies: Annotated[tuple[Literal["EUR", "CHF"], ...], Field(min_length=1, max_length=2)]
    rate_types: Annotated[tuple[RateType, ...], Field(min_length=1, max_length=6)] = (RateType.AVG,)
    period: PeriodInput


class FxRateRow(BaseModel):
    rate_type: RateType
    currency: str
    period: str
    rate: FactRef


class GetFxRatesResult(ToolResult):
    rows: list[FxRateRow]


def get_fx_rates(params: GetFxRatesInput, ctx: ToolContext) -> GetFxRatesResult:
    rates = load_fx_rates(ctx.conn)
    months = [p for p in resolve_periods(params.period.selection()) if not p.is_special]
    found = []
    for rate_type in params.rate_types:
        for currency in params.currencies:
            for period in months:
                try:
                    found.append(
                        (rate_type, currency, period, rates.rate(rate_type, currency, period))
                    )
                except FinanceError:
                    continue
    rows = [
        FxRateRow(
            rate_type=rate_type,
            currency=currency,
            period=str(period),
            rate=ctx.facts.add(
                FactKind.RATE,
                rate,
                f"USD/{currency}",
                f"{rate_type} rate, USD per {currency}, {period}",
                row_key=f"rate_type={rate_type}|currency={currency}|period={period}",
                field="rate",
            ),
        )
        for rate_type, currency, period, rate in found[:MAX_ROWS]
    ]
    warnings = [WarningCode.TRUNCATED] if len(found) > MAX_ROWS else []
    warnings += [] if rows else [WarningCode.NO_DATA]
    return GetFxRatesResult(warnings=warnings, rows=rows)
