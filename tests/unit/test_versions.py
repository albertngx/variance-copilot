from __future__ import annotations

from datetime import UTC, datetime

import pytest

from variance_copilot.finance.codes import ErrorCode, FinanceError
from variance_copilot.finance.periods import Period
from variance_copilot.finance.versions import (
    PlanKind,
    PlanVersion,
    latest_forecast,
    resolve_version,
)


def _version(category: str, year: int, kind: PlanKind, through: int, rate: str) -> PlanVersion:
    return PlanVersion(category, year, kind, through, rate, datetime(year, 1, 1, tzinfo=UTC))


VERSIONS = [
    _version("BUD25", 2025, PlanKind.BUDGET, 0, "B25"),
    _version("BUD26", 2026, PlanKind.BUDGET, 0, "B26"),
    _version("FC226", 2026, PlanKind.FORECAST, 6, "F226"),
    _version("FC126", 2026, PlanKind.FORECAST, 3, "F126"),
]
YTD_P09 = tuple(Period(2026, p) for p in range(1, 10))


def test_actuals_keep_the_periods_and_use_average_rates() -> None:
    resolved = resolve_version("ACT", YTD_P09, VERSIONS)

    assert resolved.is_actual
    assert resolved.periods == YTD_P09
    assert resolved.rate_type == "AVG"


def test_prior_year_is_the_same_periods_one_year_earlier() -> None:
    resolved = resolve_version("PY", YTD_P09, VERSIONS)

    assert resolved.is_actual
    assert resolved.periods == tuple(Period(2025, p) for p in range(1, 10))
    assert resolved.rate_type == "AVG"
    assert resolved.period_for(Period(2026, 4)) == Period(2025, 4)


def test_plan_category_resolves_with_its_rate_type() -> None:
    resolved = resolve_version("BUD26", YTD_P09, VERSIONS)

    assert resolved.plan_category == "BUD26"
    assert not resolved.is_actual
    assert resolved.periods == YTD_P09
    assert resolved.rate_type == "B26"
    assert resolved.period_for(Period(2026, 4)) == Period(2026, 4)


def test_latest_forecast_has_the_most_actual_months() -> None:
    resolved = resolve_version("LATEST_FC", YTD_P09, VERSIONS)

    assert resolved.requested == "LATEST_FC"
    assert resolved.plan_category == "FC226"
    assert resolved.rate_type == "F226"


def test_latest_forecast_breaks_ties_by_approval() -> None:
    later = PlanVersion(
        "FC226B", 2026, PlanKind.FORECAST, 6, "F226", datetime(2026, 8, 1, tzinfo=UTC)
    )

    assert latest_forecast(2026, [*VERSIONS, later]).plan_category == "FC226B"


def test_no_forecast_for_the_year() -> None:
    with pytest.raises(FinanceError) as excinfo:
        resolve_version("LATEST_FC", [Period(2025, 3)], VERSIONS)

    assert excinfo.value.code is ErrorCode.NO_FORECAST


def test_unknown_version() -> None:
    with pytest.raises(FinanceError) as excinfo:
        resolve_version("BUD27", YTD_P09, VERSIONS)

    assert excinfo.value.code is ErrorCode.UNKNOWN_VERSION


@pytest.mark.parametrize(
    ("token", "periods"),
    [
        ("BUD25", YTD_P09),
        ("BUD26", (Period(2025, 12), Period(2026, 1))),
        ("LATEST_FC", (Period(2025, 12), Period(2026, 1))),
    ],
)
def test_plan_version_must_cover_the_requested_year(
    token: str, periods: tuple[Period, ...]
) -> None:
    with pytest.raises(FinanceError) as excinfo:
        resolve_version(token, periods, VERSIONS)

    assert excinfo.value.code is ErrorCode.VERSION_PERIOD_MISMATCH
