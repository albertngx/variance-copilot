from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from variance_copilot.config import MaterialityThreshold


@given(
    absolute=st.decimals(min_value=0, max_value=10**12, places=2),
    relative=st.decimals(min_value=Decimal("0.0001"), max_value=1, places=4),
    floor=st.decimals(min_value=0, max_value=10**12, places=2),
)
def test_valid_thresholds_round_trip(absolute: Decimal, relative: Decimal, floor: Decimal) -> None:
    threshold = MaterialityThreshold(absolute=absolute, relative=relative, relative_floor=floor)

    assert MaterialityThreshold.model_validate(threshold.model_dump()) == threshold


@given(relative=st.decimals(allow_nan=False, allow_infinity=False).filter(lambda d: not 0 < d <= 1))
def test_relative_threshold_outside_unit_interval_rejected(relative: Decimal) -> None:
    with pytest.raises(ValidationError):
        MaterialityThreshold(absolute=Decimal(0), relative=relative, relative_floor=Decimal(0))
