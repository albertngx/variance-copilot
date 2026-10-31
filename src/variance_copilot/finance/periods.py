"""Fiscal periods, period views and period status (SPEC §9.11, §10.3)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from variance_copilot.finance.codes import ErrorCode, FinanceError, WarningCode

MONTHS = range(1, 13)
SPECIAL_PERIODS = range(13, 17)
LAST_MONTH = 12
QUARTER_MONTHS = 3

_PERIOD_TEXT = re.compile(r"^(\d{4})-(\d{2})$")


@dataclass(frozen=True, order=True)
class Period:
    fiscal_year: int
    period: int

    def __post_init__(self) -> None:
        if not 1 <= self.period <= SPECIAL_PERIODS[-1]:
            raise FinanceError(ErrorCode.INVALID_PERIOD, f"period {self.period} is not 1-16")

    @classmethod
    def parse(cls, text: str) -> Period:
        """``2026-05`` to ``Period(2026, 5)``."""
        match = _PERIOD_TEXT.fullmatch(text)
        if match is None:
            raise FinanceError(ErrorCode.INVALID_PERIOD, f"{text!r} is not YYYY-PP")
        return cls(int(match[1]), int(match[2]))

    @property
    def is_special(self) -> bool:
        return self.period in SPECIAL_PERIODS

    @property
    def month_start(self) -> date:
        """First day of the calendar month; special periods fall in December."""
        return date(self.fiscal_year, min(self.period, LAST_MONTH), 1)

    def shift_years(self, years: int) -> Period:
        return Period(self.fiscal_year + years, self.period)

    def __str__(self) -> str:
        return f"{self.fiscal_year}-{self.period:02d}"


class PeriodView(StrEnum):
    MTD = "MTD"
    QTD = "QTD"
    YTD = "YTD"
    FY = "FY"


@dataclass(frozen=True)
class ViewSelection:
    fiscal_year: int
    period: int
    view: PeriodView


@dataclass(frozen=True)
class RangeSelection:
    start: Period
    end: Period


type PeriodSelection = ViewSelection | RangeSelection


def _months(start: Period, end: Period) -> tuple[Period, ...]:
    if start.is_special or end.is_special:
        raise FinanceError(ErrorCode.INVALID_PERIOD, "ranges cover months 1-12 only")
    if end < start:
        raise FinanceError(ErrorCode.INVALID_PERIOD, f"range end {end} is before start {start}")
    return tuple(
        Period(year, month)
        for year in range(start.fiscal_year, end.fiscal_year + 1)
        for month in MONTHS
        if start <= Period(year, month) <= end
    )


def _view_periods(selection: ViewSelection) -> tuple[Period, ...]:
    year, last = selection.fiscal_year, selection.period
    if last not in MONTHS:
        raise FinanceError(ErrorCode.INVALID_PERIOD, f"{selection.view} needs a period of 1-12")
    match selection.view:
        case PeriodView.MTD:
            first = last
        case PeriodView.QTD:
            first = (last - 1) // QUARTER_MONTHS * QUARTER_MONTHS + 1
        case PeriodView.YTD:
            first = 1
        case PeriodView.FY:
            first, last = 1, LAST_MONTH
    periods = [Period(year, p) for p in range(first, last + 1)]
    if last == LAST_MONTH and selection.view in (PeriodView.YTD, PeriodView.FY):
        periods.extend(Period(year, p) for p in SPECIAL_PERIODS)
    return tuple(periods)


def resolve_periods(selection: PeriodSelection) -> tuple[Period, ...]:
    """Periods a view or range covers; special periods only in FY and in YTD at period 12."""
    match selection:
        case RangeSelection(start=start, end=end):
            return _months(start, end)
        case ViewSelection():
            return _view_periods(selection)


class PeriodState(StrEnum):
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class PeriodStatus:
    company_code: str
    period: Period
    state: PeriodState
    closed_at: datetime | None


def period_warnings(
    periods: Iterable[Period], company_codes: Iterable[str], statuses: Iterable[PeriodStatus]
) -> list[WarningCode]:
    """``OPEN_PERIOD`` when any requested period is open or closing for a company in scope."""
    wanted, codes = set(periods), set(company_codes)
    preliminary = any(
        s.state is not PeriodState.CLOSED and s.period in wanted and s.company_code in codes
        for s in statuses
    )
    return [WarningCode.OPEN_PERIOD] if preliminary else []
