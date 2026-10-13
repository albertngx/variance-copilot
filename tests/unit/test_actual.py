from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest

from variance_copilot.generator.actual import Layer, actual_drivers
from variance_copilot.generator.model import Drivers, Master, Rates
from variance_copilot.generator.pipeline import build_ledger, prepare
from variance_copilot.generator.plan import PlanContext, budget_drivers

RUN = prepare("ci")
SEED = 20261010


@pytest.fixture(scope="module")
def setup() -> tuple[Drivers, Master]:
    ledger = build_ledger(RUN)
    master = Master(ledger.master)
    ctx = PlanContext(RUN.scenario, master, Rates(ledger.fx_rates), RUN.profile.company_codes)
    return budget_drivers(ctx, 2026), master


def _actual(
    setup: tuple[Drivers, Master], layers: tuple[Layer, ...] = (), seed: int = SEED
) -> Drivers:
    plan, master = setup
    return actual_drivers(plan, RUN.scenario.noise, master, seed, range(1, 10), layers)


def test_noise_is_frozen_per_seed(setup: tuple[Drivers, Master]) -> None:
    first, second = _actual(setup), _actual(setup)

    assert first.sales == second.sales
    assert first.spend == second.spend
    assert first.fte == second.fte
    assert _actual(setup, seed=SEED + 1).sales != first.sales


def test_volume_noise_matches_lifecycle_sigma(setup: tuple[Drivers, Master]) -> None:
    plan, master = setup
    actual = _actual(setup)
    by_stage: dict[str, list[float]] = {}
    for key, s in actual.sales.items():
        stage = master.brand_of(key[1]).lifecycle_stage
        by_stage.setdefault(stage, []).append(math.log(s.quantity / plan.sales[key].quantity))

    for stage, logs in by_stage.items():
        sigma = float(RUN.scenario.noise.volume_sigma[stage])
        assert np.std(logs) == pytest.approx(sigma, rel=0.35)


def test_gtn_noise_stays_within_half_a_point(setup: tuple[Drivers, Master]) -> None:
    for s in _actual(setup).sales.values():
        for component, rate in s.gtn.items():
            assert abs(s.gtn_actual[component] - rate) <= 0.005 + 1e-12


def test_fte_and_spend_noise(setup: tuple[Drivers, Master]) -> None:
    plan = setup[0]
    actual = _actual(setup)
    fte = [actual.fte[k] / plan.fte[k] - 1 for k in actual.fte]
    spend = [math.log(actual.spend[k] / plan.spend[k]) for k in actual.spend if k[1] == "61050000"]

    assert np.std(fte) == pytest.approx(0.015, rel=0.3)
    assert np.std(spend) == pytest.approx(0.05, rel=0.35)


def test_overlays_are_separate_layers(setup: tuple[Drivers, Master]) -> None:
    def double_us_volume(drivers: Drivers) -> Drivers:
        sales = {
            k: replace(s, quantity=s.quantity * 2) if k[0] == "US10" else s
            for k, s in drivers.sales.items()
        }
        return replace(drivers, sales=sales)

    def add_spend(drivers: Drivers) -> Drivers:
        return replace(drivers, spend={**drivers.spend, ("CH10-RD-ONC", "61070000", 5, None): 1.0})

    base = _actual(setup)
    one = _actual(setup, (Layer("X1", double_us_volume),))
    two = _actual(setup, (Layer("X1", double_us_volume), Layer("X2", add_spend)))

    changed = {k for k in base.sales if one.sales[k] != base.sales[k]}
    assert changed == {k for k in base.sales if k[0] == "US10"}
    assert one.spend == base.spend
    assert two.sales == one.sales
    assert set(two.spend) - set(one.spend) == {("CH10-RD-ONC", "61070000", 5, None)}
