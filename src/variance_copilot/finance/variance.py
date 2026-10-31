"""Variance, favourability and magnitude (SPEC §9.3)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from variance_copilot.finance.codes import WarningCode
from variance_copilot.finance.precision import HUNDRED, with_finance_context


class Magnitude(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    FLAT = "FLAT"


@dataclass(frozen=True)
class Variance:
    actual: Decimal
    comparison: Decimal
    delta: Decimal
    delta_pct: Decimal | None
    favorable: bool
    magnitude: Magnitude
    warnings: tuple[WarningCode, ...] = ()


def magnitude(actual: Decimal, comparison: Decimal) -> Magnitude:
    """Whether the amount grew or shrank in absolute terms, regardless of sign."""
    if abs(actual) > abs(comparison):
        return Magnitude.UP
    if abs(actual) < abs(comparison):
        return Magnitude.DOWN
    return Magnitude.FLAT


@with_finance_context
def variance(actual: Decimal, comparison: Decimal) -> Variance:
    """``Δ = A - C`` in presentation sign; ``Δ%`` in percent of ``|C|``, null when ``C = 0``."""
    delta = actual - comparison
    no_base = comparison == 0
    return Variance(
        actual=actual,
        comparison=comparison,
        delta=delta,
        delta_pct=None if no_base else delta / abs(comparison) * HUNDRED,
        favorable=delta > 0,
        magnitude=magnitude(actual, comparison),
        warnings=(WarningCode.NO_COMPARISON_BASE,) if no_base else (),
    )
