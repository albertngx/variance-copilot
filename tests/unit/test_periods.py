from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from variance_copilot.finance.codes import ErrorCode, FinanceError, WarningCode
from variance_copilot.finance.periods import (
    Period,
    PeriodState,
    PeriodStatus,
    PeriodView,
    RangeSelection,
    ViewSelection,
    period_warnings,
    resolve_periods,
)

SPECIALS = [13, 14, 15, 16]


def _periods(year: int, numbers: list[int]) -> tuple[Period, ...]:
    return tuple(Period(year, p) for p in numbers)


@pytest.mark.parametrize(
    ("view", "period", "expected"),
    [
        (PeriodView.MTD, 9, [9]),
        (PeriodView.MTD, 12, [12]),
        (PeriodView.QTD, 1, [1]),
        (PeriodView.QTD, 5, [4, 5]),
        (PeriodView.QTD, 9, [7, 8, 9]),
        (PeriodView.QTD, 12, [10, 11, 12]),
        (PeriodView.YTD, 1, [1]),
        (PeriodView.YTD, 9, list(range(1, 10))),
        (PeriodView.YTD, 12, [*range(1, 13), *SPECIALS]),
        (PeriodView.FY, 3, [*range(1, 13), *SPECIALS]),
        (PeriodView.FY, 12, [*range(1, 13), *SPECIALS]),
    ],
)
def test_views_resolve_to_period_lists(view: PeriodView, period: int, expected: list[int]) -> None:
    assert resolve_periods(ViewSelection(2026, period, view)) == _periods(2026, expected)


def test_special_periods_only_in_fy_and_ytd_twelve() -> None:
    for view in PeriodView:
        for period in range(1, 13):
            periods = resolve_periods(ViewSelection(2025, period, view))
            has_special = any(p.is_special for p in periods)
            assert has_special == (
                view is PeriodView.FY or (view is PeriodView.YTD and period == 12)
            )


def test_range_within_a_year() -> None:
    selection = RangeSelection(Period.parse("2026-05"), Period.parse("2026-09"))

    assert resolve_periods(selection) == _periods(2026, [5, 6, 7, 8, 9])


def test_range_across_years_skips_special_periods() -> None:
    selection = RangeSelection(Period(2025, 11), Period(2026, 2))

    assert resolve_periods(selection) == (
        Period(2025, 11),
        Period(2025, 12),
        Period(2026, 1),
        Period(2026, 2),
    )


@pytest.mark.parametrize(
    "selection",
    [
        RangeSelection(Period(2026, 9), Period(2026, 5)),
        RangeSelection(Period(2025, 1), Period(2025, 13)),
        ViewSelection(2026, 13, PeriodView.MTD),
        ViewSelection(2026, 0, PeriodView.YTD),
    ],
)
def test_invalid_selections_are_rejected(selection: ViewSelection | RangeSelection) -> None:
    with pytest.raises(FinanceError) as excinfo:
        resolve_periods(selection)

    assert excinfo.value.code is ErrorCode.INVALID_PERIOD


@pytest.mark.parametrize("text", ["2026-5", "26-05", "2026-17", "2026-00", "May 2026"])
def test_period_parse_rejects_bad_text(text: str) -> None:
    with pytest.raises(FinanceError) as excinfo:
        Period.parse(text)

    assert excinfo.value.code is ErrorCode.INVALID_PERIOD


def test_period_text_ordering_and_months() -> None:
    assert str(Period(2026, 9)) == "2026-09"
    assert Period.parse("2025-13") == Period(2025, 13)
    assert Period(2025, 13) < Period(2026, 1)
    assert Period(2025, 14).month_start == date(2025, 12, 1)
    assert Period(2026, 3).shift_years(-1) == Period(2025, 3)


def _status(company: str, period: int, state: PeriodState) -> PeriodStatus:
    closed_at = datetime(2026, 1, 1, tzinfo=UTC) if state is PeriodState.CLOSED else None
    return PeriodStatus(company, Period(2026, period), state, closed_at)


STATUSES = [
    *(_status("ES10", p, PeriodState.CLOSED) for p in range(1, 10)),
    _status("ES10", 10, PeriodState.OPEN),
    *(_status("US10", p, PeriodState.CLOSED) for p in range(1, 9)),
    _status("US10", 9, PeriodState.CLOSING),
]


@pytest.mark.parametrize(
    ("companies", "view", "period", "warns"),
    [
        (["ES10"], PeriodView.YTD, 9, False),
        (["ES10"], PeriodView.YTD, 10, True),
        (["ES10"], PeriodView.MTD, 11, False),
        (["US10"], PeriodView.YTD, 9, True),
        (["US10"], PeriodView.MTD, 8, False),
        (["ES10", "US10"], PeriodView.QTD, 9, True),
    ],
)
def test_open_or_closing_period_warns(
    companies: list[str], view: PeriodView, period: int, warns: bool
) -> None:
    periods = resolve_periods(ViewSelection(2026, period, view))

    warnings = period_warnings(periods, companies, STATUSES)

    assert warnings == ([WarningCode.OPEN_PERIOD] if warns else [])
