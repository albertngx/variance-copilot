from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from variance_copilot.finance.codes import ErrorCode, FinanceError
from variance_copilot.finance.periods import Period
from variance_copilot.finance.translation import (
    FxRate,
    FxRates,
    LocalAmount,
    constant_currency,
    fx_split,
)
from variance_copilot.finance.versions import PlanKind, PlanVersion, resolve_version

D = Decimal
RATES = FxRates(
    [
        FxRate("B26", "CHF", date(2026, 1, 1), D("1.18")),
        FxRate("B26", "EUR", date(2026, 1, 1), D("1.12")),
        *(FxRate("AVG", "CHF", date(2025, m, 1), D("1.10") + D(m) / 100) for m in range(1, 13)),
        *(FxRate("AVG", "CHF", date(2026, m, 1), D("1.20") + D(m) / 100) for m in range(1, 13)),
    ]
)
VERSIONS = [
    PlanVersion("BUD26", 2026, PlanKind.BUDGET, 0, "B26", datetime(2025, 11, 28, tzinfo=UTC))
]


def test_rate_lookup_by_type_and_month() -> None:
    assert RATES.rate("AVG", "CHF", Period(2026, 3)) == D("1.23")
    assert RATES.rate("AVG", "CHF", Period(2025, 13)) == D("1.22")
    assert RATES.rate("B26", "CHF", Period(2026, 9)) == D("1.18")
    assert RATES.rate("B26", "USD", Period(2026, 9)) == D(1)


@pytest.mark.parametrize(
    ("rate_type", "currency", "period"),
    [
        ("B26", "CHF", Period(2025, 12)),
        ("F226", "CHF", Period(2026, 7)),
        ("AVG", "EUR", Period(2026, 1)),
    ],
)
def test_missing_rate_is_an_error(rate_type: str, currency: str, period: Period) -> None:
    with pytest.raises(FinanceError) as excinfo:
        RATES.rate(rate_type, currency, period)

    assert excinfo.value.code is ErrorCode.MISSING_FX_RATE


def test_constant_currency_against_a_plan_uses_the_version_rate_type() -> None:
    periods = [Period(2026, 1), Period(2026, 2)]
    amounts = [
        LocalAmount(Period(2026, 1), "CHF", D("-100"), D("-121")),
        LocalAmount(Period(2026, 2), "CHF", D("-50"), D("-61")),
    ]
    budget = resolve_version("BUD26", periods, VERSIONS)

    assert constant_currency(amounts, budget, RATES) == D("-150") * D("1.18")


def test_constant_currency_against_prior_year_uses_its_monthly_average() -> None:
    periods = [Period(2026, 1), Period(2026, 2)]
    amounts = [
        LocalAmount(Period(2026, 1), "CHF", D("-100"), D("-121")),
        LocalAmount(Period(2026, 2), "CHF", D("-50"), D("-61")),
    ]
    prior_year = resolve_version("PY", periods, VERSIONS)

    assert constant_currency(amounts, prior_year, RATES) == D("-100") * D("1.11") + D("-50") * D(
        "1.12"
    )


def test_worked_example_from_the_design_doc() -> None:
    """CH10 costs: CHF -105m actual at 1.26 vs CHF -100m budget at 1.18."""
    period = Period(2026, 6)
    amounts = [LocalAmount(period, "CHF", D("-105.0e6"), D("-105.0e6") * D("1.26"))]
    budget = resolve_version("BUD26", [period], VERSIONS)

    split = fx_split(amounts, D("-100.0e6") * D("1.18"), budget, RATES)

    assert split.actual_gc == D("-132.3e6")
    assert split.actual_cc == D("-123.9e6")
    assert split.delta_total == D("-14.3e6")
    assert split.fx_effect == D("-8.4e6")
    assert split.delta_operational == D("-5.9e6")


def test_usd_entity_has_no_fx_effect() -> None:
    period = Period(2026, 3)
    amounts = [LocalAmount(period, "USD", D("250.75"), D("250.75"))]
    for token in ("BUD26", "PY"):
        split = fx_split(amounts, D("200"), resolve_version(token, [period], VERSIONS), RATES)

        assert split.actual_cc == split.actual_gc
        assert split.fx_effect == 0
        assert split.delta_operational == split.delta_total


def test_mixed_currencies_translate_each_line_at_its_own_rate() -> None:
    period = Period(2026, 4)
    amounts = [
        LocalAmount(period, "CHF", D("10"), D("12.4")),
        LocalAmount(period, "EUR", D("20"), D("23")),
        LocalAmount(period, "USD", D("30"), D("30")),
    ]
    budget = resolve_version("BUD26", [period], VERSIONS)

    split = fx_split(amounts, D("60"), budget, RATES)

    assert split.actual_gc == D("65.4")
    assert split.actual_cc == D("10") * D("1.18") + D("20") * D("1.12") + D("30")
    assert split.delta_total == split.delta_operational + split.fx_effect
