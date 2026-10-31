"""Reference data the engine reads: plan versions, period status and FX rates."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Connection, text

from variance_copilot.finance.periods import Period, PeriodState, PeriodStatus
from variance_copilot.finance.translation import GROUP_CURRENCY, FxRate, FxRates
from variance_copilot.finance.versions import PlanKind, PlanVersion

_PLAN_VERSIONS = text(
    "SELECT plan_category, fiscal_year, kind, actuals_through_period, fx_rate_type, approved_at "
    "FROM fin.plan_version ORDER BY plan_category"
)
_PERIOD_STATUS = text(
    "SELECT company_code, fiscal_year, period, status, closed_at FROM fin.period_status "
    "WHERE company_code = ANY(CAST(:company_codes AS text[])) "
    "ORDER BY company_code, fiscal_year, period"
)
_FX_RATES = text(
    "SELECT rate_type, from_currency, valid_from, rate FROM md.fx_rate "
    "WHERE to_currency = :group_currency ORDER BY rate_type, from_currency, valid_from"
)


def load_plan_versions(conn: Connection) -> list[PlanVersion]:
    return [
        PlanVersion(
            plan_category=row.plan_category,
            fiscal_year=row.fiscal_year,
            kind=PlanKind(row.kind),
            actuals_through_period=row.actuals_through_period,
            fx_rate_type=row.fx_rate_type,
            approved_at=row.approved_at,
        )
        for row in conn.execute(_PLAN_VERSIONS)
    ]


def load_period_statuses(conn: Connection, company_codes: Sequence[str]) -> list[PeriodStatus]:
    rows = conn.execute(_PERIOD_STATUS, {"company_codes": list(company_codes)})
    return [
        PeriodStatus(
            company_code=row.company_code,
            period=Period(row.fiscal_year, row.period),
            state=PeriodState(row.status),
            closed_at=row.closed_at,
        )
        for row in rows
    ]


def load_fx_rates(conn: Connection) -> FxRates:
    rows = conn.execute(_FX_RATES, {"group_currency": GROUP_CURRENCY})
    return FxRates(
        FxRate(row.rate_type, row.from_currency, row.valid_from, row.rate) for row in rows
    )
