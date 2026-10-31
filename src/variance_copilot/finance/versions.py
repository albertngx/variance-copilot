"""Version tokens: actuals, plan versions, prior year and latest forecast (SPEC §7.9, §10.3)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from variance_copilot.finance.codes import ErrorCode, FinanceError
from variance_copilot.finance.periods import Period

ACTUAL = "ACT"
PRIOR_YEAR = "PY"
LATEST_FORECAST = "LATEST_FC"
ACTUAL_RATE_TYPE = "AVG"


class PlanKind(StrEnum):
    BUDGET = "BUDGET"
    FORECAST = "FORECAST"


@dataclass(frozen=True)
class PlanVersion:
    plan_category: str
    fiscal_year: int
    kind: PlanKind
    actuals_through_period: int
    fx_rate_type: str
    approved_at: datetime


@dataclass(frozen=True)
class ResolvedVersion:
    """A requested version bound to its source, periods and translation rates."""

    requested: str
    plan_category: str | None
    periods: tuple[Period, ...]
    rate_type: str
    year_offset: int = 0

    @property
    def is_actual(self) -> bool:
        return self.plan_category is None

    def period_for(self, period: Period) -> Period:
        """The period of this version that corresponds to a requested period."""
        return period.shift_years(self.year_offset)


def latest_forecast(fiscal_year: int, versions: Iterable[PlanVersion]) -> PlanVersion:
    """The forecast for the year with the most months of actuals."""
    forecasts = [
        v for v in versions if v.kind is PlanKind.FORECAST and v.fiscal_year == fiscal_year
    ]
    if not forecasts:
        raise FinanceError(ErrorCode.NO_FORECAST, f"no approved forecast for {fiscal_year}")
    return max(forecasts, key=lambda v: (v.actuals_through_period, v.approved_at))


def _single_year(periods: Sequence[Period]) -> int:
    years = {p.fiscal_year for p in periods}
    if len(years) != 1:
        raise FinanceError(
            ErrorCode.VERSION_PERIOD_MISMATCH, "plan versions cover a single fiscal year"
        )
    return years.pop()


def resolve_version(
    requested: str, periods: Sequence[Period], versions: Iterable[PlanVersion]
) -> ResolvedVersion:
    """Resolve ``ACT``, ``PY``, ``LATEST_FC`` or a plan category for the requested periods."""
    if requested == ACTUAL:
        return ResolvedVersion(requested, None, tuple(periods), ACTUAL_RATE_TYPE)
    if requested == PRIOR_YEAR:
        shifted = tuple(p.shift_years(-1) for p in periods)
        return ResolvedVersion(requested, None, shifted, ACTUAL_RATE_TYPE, year_offset=-1)

    fiscal_year = _single_year(periods)
    if requested == LATEST_FORECAST:
        version = latest_forecast(fiscal_year, versions)
    else:
        by_category = {v.plan_category: v for v in versions}
        if requested not in by_category:
            raise FinanceError(ErrorCode.UNKNOWN_VERSION, f"unknown version {requested!r}")
        version = by_category[requested]
    if version.fiscal_year != fiscal_year:
        raise FinanceError(
            ErrorCode.VERSION_PERIOD_MISMATCH,
            f"{version.plan_category} covers {version.fiscal_year}, not {fiscal_year}",
        )
    return ResolvedVersion(requested, version.plan_category, tuple(periods), version.fx_rate_type)
