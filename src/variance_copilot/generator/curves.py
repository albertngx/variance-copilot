"""Volume curves of the revenue model (SPEC §8.4). Months are indexed from January 2025."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date

from variance_copilot.generator.assumptions import Erosion, Lifecycle

BASE_YEAR = 2025


def month_index(year: int, month: int) -> int:
    return (year - BASE_YEAR) * 12 + month - 1


def month_index_of(value: str | date) -> int:
    if isinstance(value, date):
        return month_index(value.year, value.month)
    return month_index(int(value[:4]), int(value[5:7]))


def lifecycle(curve: Lifecycle, t: int, launch: int | None = None) -> float:
    """L_b(t): logistic after launch, or compound annual growth or decline."""
    if curve.stage == "LAUNCH":
        if launch is None or t < launch:
            return 0.0
        midpoint = launch + curve.midpoint_months_after_launch
        return 1 / (1 + math.exp(-float(curve.steepness) * (t - midpoint)))
    if curve.stage == "DECLINE":
        return float((1 - float(curve.decline)) ** (t / 12))
    return float((1 + float(curve.growth)) ** (t / 12))


def loe_erosion(erosion: Erosion, t: int, loe: int | None) -> float:
    """E(t): 1 before loss of exclusivity, then 1 - alpha * (1 - exp(-beta * months since))."""
    if loe is None or t < loe:
        return 1.0
    months_since = t - loe + 1
    return 1 - float(erosion.alpha) * (1 - math.exp(-float(erosion.beta) * months_since))


def normalised(weights: Sequence[float]) -> list[float]:
    mean = sum(weights) / len(weights)
    return [w / mean for w in weights]
