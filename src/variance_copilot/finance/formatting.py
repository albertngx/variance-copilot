"""Presentation formatting of amounts, percentages, rates and FTE (SPEC §9.10)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from variance_copilot.finance.precision import ONE, with_finance_context

MINUS = "\N{MINUS SIGN}"
THOUSAND = Decimal(1000)


@dataclass(frozen=True)
class _Unit:
    suffix: str
    exponent: int
    places: Decimal

    @property
    def floor(self) -> Decimal:
        return ONE.scaleb(self.exponent)


_UNITS = (
    _Unit("", 0, Decimal(1)),
    _Unit("k", 3, Decimal(1)),
    _Unit("m", 6, Decimal("0.1")),
    _Unit("bn", 9, Decimal("0.01")),
)
_PERCENT_PLACES = Decimal("0.1")
_RATE_PLACES = Decimal("0.0001")
_WHOLE = Decimal(1)


def _round(value: Decimal, places: Decimal) -> Decimal:
    return value.quantize(places, rounding=ROUND_HALF_UP)


def _signed(value: Decimal, rounded: Decimal, text: str, absolute: bool) -> str:
    return f"{MINUS}{text}" if value < 0 and rounded and not absolute else text


def _scaled(magnitude: Decimal, unit: _Unit) -> Decimal:
    return _round(magnitude.scaleb(-unit.exponent), unit.places)


@with_finance_context
def format_amount(value: Decimal, currency: str = "USD", *, absolute: bool = False) -> str:
    """``USD 1.23bn``, ``USD 12.3m``, ``USD 456k`` or whole units; 1,000 after rounding moves up."""
    magnitude = abs(value)
    index = max((i for i, unit in enumerate(_UNITS) if magnitude >= unit.floor), default=0)
    scaled = _scaled(magnitude, _UNITS[index])
    if scaled >= THOUSAND and index < len(_UNITS) - 1:
        index += 1
        scaled = _scaled(magnitude, _UNITS[index])
    return _signed(value, scaled, f"{currency} {scaled:,f}{_UNITS[index].suffix}", absolute)


@with_finance_context
def format_percent(value: Decimal, *, absolute: bool = False) -> str:
    """A percentage given in percent units, one decimal: ``8.1%``."""
    rounded = _round(abs(value), _PERCENT_PLACES)
    return _signed(value, rounded, f"{rounded:,f}%", absolute)


@with_finance_context
def format_pp(value: Decimal, *, absolute: bool = False) -> str:
    """Percentage points, one decimal: ``2.4 pp``."""
    rounded = _round(abs(value), _PERCENT_PLACES)
    return _signed(value, rounded, f"{rounded:,f} pp", absolute)


@with_finance_context
def format_fte(value: Decimal, *, absolute: bool = False) -> str:
    """Full-time equivalents as a whole number: ``1,235 FTE``."""
    rounded = _round(abs(value), _WHOLE)
    return _signed(value, rounded, f"{rounded:,f} FTE", absolute)


@with_finance_context
def format_rate(value: Decimal, *, absolute: bool = False) -> str:
    """An exchange rate, four decimals: ``1.2589``."""
    rounded = _round(abs(value), _RATE_PLACES)
    return _signed(value, rounded, f"{rounded:,f}", absolute)
