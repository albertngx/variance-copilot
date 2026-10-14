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


class _Streams:
    """One random stream per driver family, year and company, created on first use."""

    def __init__(self, seed: int, year: int) -> None:
        self._seed, self._year = seed, year
        self._streams: dict[tuple[str, str], np.random.Generator] = {}

    def __call__(self, family: str, company_code: str) -> np.random.Generator:
        key = (family, company_code)
        if key not in self._streams:
            name = f"noise.{family}.{self._year}.{company_code}"
            self._streams[key] = module_rng(self._seed, name)
        return self._streams[key]


def noisy_drivers(
    plan: Drivers, noise: NoiseAssumptions, master: Master, seed: int, periods: Sequence[int]
) -> Drivers:
    """Actual drivers before events; a company's noise does not depend on other companies."""
    stream = _Streams(seed, plan.fiscal_year)
    pp = float(noise.gtn_rate_pp)

    price_noise = {
        (c, m, p): 1 + float(noise.net_price_sigma) * stream("price", c).standard_normal()
        for c, m, p in sorted({(c, m, p) for c, m, _, p in plan.sales if p in periods})
    }
    sales = {}
    for key in sorted(k for k in plan.sales if k[3] in periods):
        company_code, material, _, period = key
        s = plan.sales[key]
        sigma = float(noise.volume_sigma[master.brand_of(material).lifecycle_stage])
        quantity = s.quantity * _lognormal(stream("volume", company_code), sigma)
        gtn = stream("gtn", company_code)
        actual_rates = {c: max(0.0, r + gtn.uniform(-pp, pp)) for c, r in sorted(s.gtn.items())}
        list_price = s.list_price * price_noise[company_code, material, period]
        sales[key] = Sales(quantity, list_price, dict(s.gtn), actual_rates)

    def company(cost_center: str) -> str:
        return master.cost_centers[cost_center].company_code

    fte = {
        key: value * (1 + float(noise.fte_sigma) * stream("fte", company(key[0])).standard_normal())
        for key, value in sorted(plan.fte.items())
        if key[1] in periods
    }
    spend = {
        key: value * _lognormal(stream("spend", company(key[0])), float(noise.spend(key[1])))
        for key, value in sorted(plan.spend.items(), key=lambda item: str(item[0]))
        if key[2] in periods
    }
    production_variance = {
        (material, period): stream("production_variance", "IE10").uniform(-1, 1)
        * float(noise.production_variance)
        for period in periods
        for material in sorted(plan.standard_cost)
    }
    companies = sorted({company(cc) for cc, _ in plan.fte})
    return replace(
        plan,
        version=ACTUAL,
        periods=list(periods),
        sales=sales,
        fte=fte,
        spend=spend,
        depreciation={k: v for k, v in plan.depreciation.items() if k[1] in periods},
        production_variance=production_variance,
        bonus_true_up={c: 0.05 * stream("bonus", c).standard_normal() for c in companies},
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
