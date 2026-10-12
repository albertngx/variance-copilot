from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from variance_copilot.generator.calendar import (
    close_timestamp,
    fiscal_periods,
    period_statuses,
    working_days,
)
from variance_copilot.generator.config import load_scenario

CONFIG = load_scenario().calendar
AS_OF = date(2026, 10, 9)
COMPANIES = ["CH10", "DE10", "ES10", "IE10", "US10"]


@pytest.fixture(scope="module")
def statuses() -> dict[tuple[str, int, int], tuple[str, datetime | None]]:
    rows = period_statuses(CONFIG, COMPANIES, [2025, 2026], AS_OF)
    return {(r.company_code, r.fiscal_year, r.period): (r.status, r.closed_at) for r in rows}


def test_fiscal_periods_include_special_periods_on_31_december() -> None:
    periods = {(p.fiscal_year, p.period): p for p in fiscal_periods([2026])}

    assert len(periods) == 16
    assert periods[2026, 2].end_date == date(2026, 2, 28)
    for period in range(13, 17):
        assert periods[2026, period].is_special
        assert periods[2026, period].start_date == periods[2026, period].end_date
        assert periods[2026, period].end_date == date(2026, 12, 31)
    assert not any(periods[2026, p].is_special for p in range(1, 13))


def test_working_days_skip_weekends_and_holidays() -> None:
    january = working_days(2026, 1, CONFIG.holidays)

    assert january[:4] == [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]
    assert all(day.weekday() < 5 for day in january)


def test_close_is_working_day_four_of_next_month_at_local_close_time() -> None:
    closed_at = close_timestamp(CONFIG, "US10", 2026, 9)

    assert closed_at == datetime(2026, 10, 6, 18, 0, tzinfo=ZoneInfo("America/New_York"))
    for company in COMPANIES:
        local = close_timestamp(CONFIG, company, 2026, 5)
        assert local.date() == working_days(2026, 6, CONFIG.holidays)[3]
        assert local.timetz() == time(18, 0, tzinfo=ZoneInfo(CONFIG.time_zones[company]))


def test_december_closes_in_january_of_the_next_year() -> None:
    assert close_timestamp(CONFIG, "CH10", 2025, 12).date() == date(2026, 1, 7)


def test_close_state_on_as_of_date(statuses: dict) -> None:
    for company in COMPANIES:
        for period in range(1, 14):
            assert statuses[company, 2025, period][0] == "CLOSED"
        for period in range(1, 10):
            assert statuses[company, 2026, period][0] == "CLOSED"
        assert statuses[company, 2026, 10] == ("OPEN", None)
        for period in (*range(11, 17),):
            assert (company, 2026, period) not in statuses
        for period in range(14, 17):
            assert (company, 2025, period) not in statuses


def test_closed_at_is_after_the_period_ends(statuses: dict) -> None:
    periods = {(p.fiscal_year, p.period): p for p in fiscal_periods([2025, 2026])}
    for (_, year, period), (status, closed_at) in statuses.items():
        if status == "CLOSED":
            assert closed_at.date() > periods[year, period].end_date
