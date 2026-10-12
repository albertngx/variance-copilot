"""Actual drivers: plan drivers x frozen noise, then event overlays (SPEC §8.3 step 5, §8.9)."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

import numpy as np

from variance_copilot.generator.assumptions import NoiseAssumptions
from variance_copilot.generator.model import ACTUAL, Drivers, Master, Sales
from variance_copilot.generator.rng import module_rng

Overlay = Callable[[Drivers], Drivers]


@dataclass(frozen=True)
class Layer:
    """A named event overlay; applied in order so each event's effect can be isolated."""

    event_id: str
    apply: Overlay


def _lognormal(rng: np.random.Generator, sigma: float) -> float:
    return math.exp(sigma * rng.standard_normal() - sigma**2 / 2)


def noisy_drivers(
    plan: Drivers, noise: NoiseAssumptions, master: Master, seed: int, periods: Sequence[int]
) -> Drivers:
    """Actual drivers before events; each driver family draws from its own stream."""
    year = plan.fiscal_year

    def stream(name: str) -> np.random.Generator:
        return module_rng(seed, f"noise.{name}.{year}")

    volume, price, gtn = stream("volume"), stream("price"), stream("gtn")
    price_noise = {
        key: 1 + float(noise.net_price_sigma) * price.standard_normal()
        for key in sorted({(c, m, p) for c, m, _, p in plan.sales if p in periods})
    }
    sales = {}
    for key in sorted(k for k in plan.sales if k[3] in periods):
        company_code, material, _, period = key
        s = plan.sales[key]
        stage = master.brand_of(material).lifecycle_stage
        quantity = s.quantity * _lognormal(volume, float(noise.volume_sigma[stage]))
        pp = float(noise.gtn_rate_pp)
        actual_rates = {c: max(0.0, r + gtn.uniform(-pp, pp)) for c, r in sorted(s.gtn.items())}
        list_price = s.list_price * price_noise[company_code, material, period]
        sales[key] = Sales(quantity, list_price, dict(s.gtn), actual_rates)

    fte_stream = stream("fte")
    fte = {
        key: value * (1 + float(noise.fte_sigma) * fte_stream.standard_normal())
        for key, value in sorted(plan.fte.items())
        if key[1] in periods
    }
    spend_stream = stream("spend")
    spend = {
        key: value * _lognormal(spend_stream, float(noise.spend(key[1])))
        for key, value in sorted(plan.spend.items(), key=lambda item: str(item[0]))
        if key[2] in periods
    }
    variance = stream("production_variance")
    materials = sorted(plan.standard_cost)
    production_variance = {
        (material, period): variance.uniform(-1, 1) * float(noise.production_variance)
        for period in periods
        for material in materials
    }
    bonus = stream("bonus")
    companies = sorted({master.cost_centers[cc].company_code for cc, _ in plan.fte})
    return replace(
        plan,
        version=ACTUAL,
        periods=list(periods),
        sales=sales,
        fte=fte,
        spend=spend,
        depreciation={k: v for k, v in plan.depreciation.items() if k[1] in periods},
        production_variance=production_variance,
        bonus_true_up={c: 0.05 * bonus.standard_normal() for c in companies},
    )


def actual_drivers(
    plan: Drivers,
    noise: NoiseAssumptions,
    master: Master,
    seed: int,
    periods: Sequence[int],
    layers: Sequence[Layer] = (),
) -> Drivers:
    drivers = noisy_drivers(plan, noise, master, seed, periods)
    for layer in layers:
        drivers = layer.apply(drivers)
    return drivers
