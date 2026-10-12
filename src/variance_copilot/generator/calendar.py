"""Fiscal calendar, working days and period close state (SPEC §8.2)."""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Connection

from variance_copilot.generator.config import CalendarConfig
from variance_copilot.generator.db import upsert

MONTHS = range(1, 13)
SPECIAL_PERIODS = range(13, 17)
YEAR_END_ADJUSTMENT_PERIOD = 13


@dataclass(frozen=True)
class FiscalPeriod:
    fiscal_year: int
    period: int
    start_date: date
    end_date: date
    is_special: bool


@dataclass(frozen=True)
class PeriodStatus:
    company_code: str
    fiscal_year: int
    period: int
    status: str
    closed_at: datetime | None


def fiscal_periods(years: Iterable[int]) -> list[FiscalPeriod]:
    periods = []
    for year in years:
        for month in MONTHS:
            last_day = calendar.monthrange(year, month)[1]
            periods.append(
                FiscalPeriod(year, month, date(year, month, 1), date(year, month, last_day), False)
            )
        year_end = date(year, 12, 31)
        periods.extend(FiscalPeriod(year, p, year_end, year_end, True) for p in SPECIAL_PERIODS)
    return periods


def working_days(year: int, month: int, holidays: Iterable[str] = ()) -> list[date]:
    """Weekdays of the month, excluding ``MM-DD`` holidays."""
    excluded = set(holidays)
    return [
        day
        for week in calendar.Calendar().monthdatescalendar(year, month)
        for day in week
        if day.month == month and day.weekday() < 5 and day.strftime("%m-%d") not in excluded
    ]


def close_timestamp(config: CalendarConfig, company_code: str, year: int, period: int) -> datetime:
    """Working day N of the month after the period, at the close time in local time."""
    if period == YEAR_END_ADJUSTMENT_PERIOD:
        close_year, close_month = year + 1, config.special_period_close_month
    else:
        close_year, close_month = (year + 1, 1) if period == 12 else (year, period + 1)
    day = working_days(close_year, close_month, config.holidays)[config.close_working_day - 1]
    return datetime.combine(day, config.close_time, ZoneInfo(config.time_zones[company_code]))


def period_statuses(
    config: CalendarConfig, company_codes: Iterable[str], years: Iterable[int], as_of: date
) -> list[PeriodStatus]:
    """Closed periods up to ``as_of``, and the open period that contains it."""
    statuses = []
    for company_code in company_codes:
        for year in years:
            for period in [*MONTHS, YEAR_END_ADJUSTMENT_PERIOD]:
                closed_at = close_timestamp(config, company_code, year, period)
                if closed_at.date() <= as_of:
                    statuses.append(PeriodStatus(company_code, year, period, "CLOSED", closed_at))
                elif period in MONTHS and date(year, period, 1) <= as_of:
                    statuses.append(PeriodStatus(company_code, year, period, "OPEN", None))
    return statuses


def write_calendar(
    conn: Connection, periods: list[FiscalPeriod], statuses: list[PeriodStatus]
) -> None:
    upsert(conn, "md.fiscal_period", [vars(p) for p in periods], ("fiscal_year", "period"))
    upsert(
        conn,
        "fin.period_status",
        [vars(s) for s in statuses],
        ("company_code", "fiscal_year", "period"),
    )
