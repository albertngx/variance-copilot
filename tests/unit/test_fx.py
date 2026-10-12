from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
import pytest

from variance_copilot.generator.config import load_scenario
from variance_copilot.generator.fx import FxRate, generate_fx_rates
from variance_copilot.generator.rng import module_rng

SCENARIO = load_scenario()
CONFIG = SCENARIO.fx
SEED = 20261010


def _rates(seed: int = SEED, apply_overrides: bool = True) -> list[FxRate]:
    return generate_fx_rates(
        CONFIG, module_rng(seed, "fx"), SCENARIO.actuals_from, SCENARIO.as_of, apply_overrides
    )


def _by_key(rates: list[FxRate]) -> dict[tuple[str, str, date], Decimal]:
    return {(r.rate_type, r.from_currency, r.valid_from): r.rate for r in rates}


@pytest.fixture(scope="module")
def rates() -> dict[tuple[str, str, date], Decimal]:
    return _by_key(_rates())


def _average(rates: dict, currency: str, year: int, month: int) -> float:
    return float(rates["AVG", currency, date(year, month, 1)])


def test_budget_rates_are_exact(rates: dict) -> None:
    assert rates["B25", "EUR", date(2025, 1, 1)] == Decimal("1.0800")
    assert rates["B25", "CHF", date(2025, 1, 1)] == Decimal("1.1200")
    assert rates["B26", "EUR", date(2026, 1, 1)] == Decimal("1.1200")
    assert rates["B26", "CHF", date(2026, 1, 1)] == Decimal("1.1800")


def test_forecast_rates_are_the_cut_off_month_average(rates: dict) -> None:
    for currency in ("EUR", "CHF"):
        assert rates["F126", currency, date(2026, 1, 1)] == rates["AVG", currency, date(2026, 3, 1)]
        assert rates["F226", currency, date(2026, 1, 1)] == rates["AVG", currency, date(2026, 6, 1)]


def test_average_rates_cover_every_actual_month(rates: dict) -> None:
    months = sorted(d for (t, c, d) in rates if t == "AVG" and c == "EUR")

    assert months[0] == date(2025, 1, 1)
    assert months[-1] == date(2026, 10, 1)
    assert len(months) == 22


def test_chf_follows_the_e02_override(rates: dict) -> None:
    for month, target in CONFIG.overrides["CHF"].items():
        actual = _average(rates, "CHF", int(month[:4]), int(month[5:]))
        assert abs(actual / float(target) - 1) <= 0.003 + 1e-6


def test_other_averages_stay_within_four_percent_of_budget(rates: dict) -> None:
    overridden = {(int(m[:4]), int(m[5:])) for m in CONFIG.overrides["CHF"]}
    for (rate_type, currency, valid_from), rate in rates.items():
        if rate_type != "AVG" or (
            currency == "CHF" and (valid_from.year, valid_from.month) in overridden
        ):
            continue
        budget = rates[f"B{valid_from.year % 100}", currency, date(valid_from.year, 1, 1)]
        assert abs(float(rate) / float(budget) - 1) <= 0.04 + 1e-6


def test_closing_rate_is_next_month_average_with_small_noise(rates: dict) -> None:
    ratios = [
        float(rate) / float(rates["AVG", currency, valid_from]) - 1
        for (rate_type, currency, valid_from), rate in rates.items()
        if rate_type == "CLO"
    ]

    assert len(ratios) == 2 * 21
    assert max(abs(r) for r in ratios) < 5 * 0.004
    assert 0.001 < float(np.std(ratios)) < 0.008


def test_same_seed_gives_identical_rates() -> None:
    assert _rates() == _rates()
    assert _rates() != _rates(seed=SEED + 1)


def test_overrides_do_not_change_other_paths() -> None:
    with_e02 = _by_key(_rates(apply_overrides=True))
    without_e02 = _by_key(_rates(apply_overrides=False))

    assert {k: v for k, v in with_e02.items() if k[1] == "EUR"} == {
        k: v for k, v in without_e02.items() if k[1] == "EUR"
    }
    assert without_e02["AVG", "CHF", date(2026, 6, 1)] < Decimal("1.2272")  # B26 + 4%
