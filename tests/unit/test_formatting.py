from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, getcontext, localcontext

import pytest

from variance_copilot.finance.formatting import (
    format_amount,
    format_fte,
    format_percent,
    format_pp,
    format_rate,
)
from variance_copilot.finance.precision import PRECISION, with_finance_context

M = "\N{MINUS SIGN}"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", "USD 0"),
        ("0.49", "USD 0"),
        ("0.5", "USD 1"),
        ("999.49", "USD 999"),
        ("999.5", "USD 1k"),
        ("1000", "USD 1k"),
        ("456789", "USD 457k"),
        ("950000", "USD 950k"),
        ("999499.99", "USD 999k"),
        ("999500", "USD 1.0m"),
        ("999950", "USD 1.0m"),
        ("1000000", "USD 1.0m"),
        ("12345678", "USD 12.3m"),
        ("12350000", "USD 12.4m"),
        ("999949999", "USD 999.9m"),
        ("999950000", "USD 1.00bn"),
        ("1234567890", "USD 1.23bn"),
        ("1234567890123", "USD 1,234.57bn"),
    ],
)
def test_amount_units_and_boundaries(value: str, expected: str) -> None:
    assert format_amount(Decimal(value)) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("-12345678", f"{M}USD 12.3m"),
        ("-999950", f"{M}USD 1.0m"),
        ("-0.5", f"{M}USD 1"),
        ("-0.49", "USD 0"),
        ("-0", "USD 0"),
    ],
)
def test_negative_amounts_lead_with_minus_unless_they_round_to_zero(
    value: str, expected: str
) -> None:
    assert format_amount(Decimal(value)) == expected


def test_abs_modifier_drops_the_sign() -> None:
    assert format_amount(Decimal("-12345678"), absolute=True) == "USD 12.3m"
    assert format_percent(Decimal("-8.06"), absolute=True) == "8.1%"
    assert format_pp(Decimal("-2.44"), absolute=True) == "2.4 pp"


def test_amount_in_another_currency() -> None:
    assert format_amount(Decimal("-22000000"), "CHF") == f"{M}CHF 22.0m"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("8.05", "8.1%"),
        ("8.04", "8.0%"),
        ("0", "0.0%"),
        ("-0.04", "0.0%"),
        ("-10.506", f"{M}10.5%"),
        ("1234.56", "1,234.6%"),
    ],
)
def test_percent_one_decimal(value: str, expected: str) -> None:
    assert format_percent(Decimal(value)) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("2.45", "2.5 pp"), ("0", "0.0 pp"), ("-0.35", f"{M}0.4 pp")],
)
def test_percentage_points_one_decimal(value: str, expected: str) -> None:
    assert format_pp(Decimal(value)) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1234.5", "1,235 FTE"), ("12.4", "12 FTE"), ("0", "0 FTE"), ("-3.5", f"{M}4 FTE")],
)
def test_fte_whole_numbers(value: str, expected: str) -> None:
    assert format_fte(Decimal(value)) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1.258947", "1.2589"), ("1.12005", "1.1201"), ("1", "1.0000")],
)
def test_rates_four_decimals(value: str, expected: str) -> None:
    assert format_rate(Decimal(value)) == expected


def test_finance_context_ignores_the_callers_context() -> None:
    @with_finance_context
    def third() -> Decimal:
        return Decimal(1) / Decimal(3)

    with localcontext() as caller:
        caller.prec = 5
        caller.rounding = ROUND_HALF_UP
        result = third()
        assert getcontext().prec == 5

    assert len(result.as_tuple().digits) == PRECISION


def test_half_even_inside_the_engine_half_up_when_formatting() -> None:
    @with_finance_context
    def halve(value: Decimal) -> Decimal:
        return (value / 2).quantize(Decimal(1))

    assert halve(Decimal(5)) == Decimal(2)
    assert format_amount(Decimal("2.5")) == "USD 3"


def test_formatting_ignores_a_low_precision_caller_context() -> None:
    with localcontext() as caller:
        caller.prec = 3
        assert format_amount(Decimal("1234567890123")) == "USD 1,234.57bn"
