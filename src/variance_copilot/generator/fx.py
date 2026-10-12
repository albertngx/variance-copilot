"""Exchange-rate paths (SPEC §7.8, §8.6). Rates are USD per unit of currency."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

import numpy as np
from sqlalchemy import Connection

from variance_copilot.generator.config import Currency, FxConfig
from variance_copilot.generator.db import upsert

GROUP_CURRENCY = "USD"
CURRENCIES: tuple[Currency, ...] = ("EUR", "CHF")
RATE_PLACES = Decimal("0.000001")


@dataclass(frozen=True)
class FxRate:
    rate_type: str
    from_currency: str
    to_currency: str
    valid_from: date
    rate: Decimal


def months_between(first: date, last: date) -> list[date]:
    months = []
    month = first.replace(day=1)
    while month <= last:
        months.append(month)
        month = month.replace(year=month.year + month.month // 12, month=month.month % 12 + 1)
    return months


def _round(value: float) -> Decimal:
    return Decimal(str(value)).quantize(RATE_PLACES, rounding=ROUND_HALF_EVEN)


def _budget_rate(config: FxConfig, year: int, currency: Currency) -> Decimal:
    for rate_type, rates in config.budget.items():
        if rate_type == f"B{year % 100:02d}":
            return rates[currency]
    raise ValueError(f"no budget rate for {year}")


def generate_fx_rates(
    config: FxConfig,
    rng: np.random.Generator,
    first_month: date,
    last_month: date,
    apply_overrides: bool,
) -> list[FxRate]:
    """AVG, CLO, budget and forecast rates; draws are fixed-shape so overrides shift nothing."""
    months = months_between(first_month, last_month)
    shape = (len(CURRENCIES), len(months))
    walk_steps = rng.standard_normal(shape)
    override_noise = rng.uniform(-1.0, 1.0, shape)
    closing_noise = rng.standard_normal(shape)
    low, high = math.log(1 - config.clip_to_budget), math.log(1 + config.clip_to_budget)

    rates: list[FxRate] = []
    for c, currency in enumerate(CURRENCIES):
        sigma = float(config.random_walk_sigma[currency])
        overrides = config.overrides.get(currency, {}) if apply_overrides else {}
        deviation = 0.0
        average: dict[date, Decimal] = {}
        for m, month in enumerate(months):
            deviation = min(max(deviation + sigma * walk_steps[c, m], low), high)
            override = overrides.get(month.strftime("%Y-%m"))
            if override is None:
                budget = float(_budget_rate(config, month.year, currency))
                average[month] = _round(budget * math.exp(deviation))
            else:
                noise = float(config.override_noise) * override_noise[c, m]
                average[month] = _round(float(override) * (1 + noise))

        sigma_close = float(config.closing_noise_sigma)
        closing = {
            months[m + 1]: _round(float(average[months[m + 1]]) * (1 + sigma_close * noise))
            for m, noise in enumerate(closing_noise[c, :-1])
        }

        rates += [FxRate("AVG", currency, GROUP_CURRENCY, d, r) for d, r in average.items()]
        rates += [FxRate("CLO", currency, GROUP_CURRENCY, d, r) for d, r in closing.items()]
        rates += [
            FxRate(rate_type, currency, GROUP_CURRENCY, date(2000 + int(rate_type[1:]), 1, 1),
                   budget_rates[currency])
            for rate_type, budget_rates in config.budget.items()
        ]  # fmt: skip
        rates += [
            FxRate(rate_type, currency, GROUP_CURRENCY, date(int(month[:4]), 1, 1),
                   average[date(int(month[:4]), int(month[5:]), 1)])
            for rate_type, month in config.forecast_rate_months.items()
        ]  # fmt: skip
    return rates


def write_fx_rates(conn: Connection, rates: list[FxRate]) -> None:
    upsert(
        conn,
        "md.fx_rate",
        [vars(rate) for rate in rates],
        ("rate_type", "from_currency", "to_currency", "valid_from"),
    )
