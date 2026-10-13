from __future__ import annotations

import math
from decimal import Decimal

import pytest

from variance_copilot.generator.assumptions import Erosion, Lifecycle
from variance_copilot.generator.curves import lifecycle, loe_erosion, month_index, normalised


def test_month_index_starts_in_january_2025() -> None:
    assert month_index(2025, 1) == 0
    assert month_index(2026, 10) == 21


def test_launch_curve_is_zero_before_launch_and_half_at_midpoint() -> None:
    curve = Lifecycle(stage="LAUNCH", steepness=Decimal("0.35"), midpoint_months_after_launch=2)

    assert lifecycle(curve, 7, launch=8) == 0.0
    assert lifecycle(curve, 10, launch=8) == pytest.approx(0.5)
    assert lifecycle(curve, 40, launch=8) == pytest.approx(1.0, abs=1e-4)
    values = [lifecycle(curve, t, launch=8) for t in range(8, 30)]
    assert values == sorted(values)


@pytest.mark.parametrize(
    ("curve", "after_one_year"),
    [
        (Lifecycle(stage="GROWTH", growth=Decimal("0.15")), 1.15),
        (Lifecycle(stage="MATURE", growth=Decimal("-0.02")), 0.98),
        (Lifecycle(stage="DECLINE", decline=Decimal("0.10")), 0.90),
    ],
)
def test_compound_curves(curve: Lifecycle, after_one_year: float) -> None:
    assert lifecycle(curve, 0) == 1.0
    assert lifecycle(curve, 12) == pytest.approx(after_one_year)


def test_loe_erosion() -> None:
    erosion = Erosion(alpha=Decimal("0.8"), beta=Decimal("0.35"))

    assert loe_erosion(erosion, 19, loe=20) == 1.0
    assert loe_erosion(erosion, 20, loe=20) == pytest.approx(1 - 0.8 * (1 - math.exp(-0.35)))
    assert loe_erosion(erosion, 200, loe=20) == pytest.approx(0.2)
    assert loe_erosion(erosion, 30, loe=None) == 1.0


def test_normalised_weights_average_one() -> None:
    weights = normalised([0.9, 1.0, 1.1, 0.8])

    assert sum(weights) / len(weights) == pytest.approx(1.0)
    assert weights[2] / weights[0] == pytest.approx(1.1 / 0.9)
