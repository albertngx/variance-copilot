"""Generator pipeline (SPEC §8.3). Steps 1-3 so far: master data, calendar, FX."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Connection

from variance_copilot.generator import calendar, fx, master
from variance_copilot.generator.config import (
    DEFAULT_PROFILES,
    DEFAULT_SCENARIO,
    Profile,
    ScenarioConfig,
    load_profiles,
    load_scenario,
)
from variance_copilot.generator.rng import module_rng

FX_EVENT = "E02"


@dataclass(frozen=True)
class Run:
    scenario: ScenarioConfig
    profile: Profile
    seed: int


def prepare(
    profile_name: str,
    seed: int | None = None,
    scenario_path: Path = DEFAULT_SCENARIO,
    profiles_path: Path = DEFAULT_PROFILES,
) -> Run:
    scenario = load_scenario(scenario_path)
    profiles = load_profiles(profiles_path).profiles
    if profile_name not in profiles:
        raise ValueError(f"unknown profile {profile_name!r}; choose from {sorted(profiles)}")
    return Run(scenario, profiles[profile_name], scenario.seed if seed is None else seed)


def load_reference_data(
    conn: Connection, run: Run, master_dir: Path = master.DEFAULT_SEEDS_DIR
) -> dict[str, int]:
    """Steps 1-3: master data, fiscal calendar and period status, exchange rates."""
    counts = master.write_master_data(conn, master.read_master_data(master_dir))

    periods = calendar.fiscal_periods(run.profile.fiscal_years)
    statuses = calendar.period_statuses(
        run.scenario.calendar,
        run.profile.company_codes,
        run.profile.fiscal_years,
        run.scenario.as_of,
    )
    calendar.write_calendar(conn, periods, statuses)

    rates = fx.generate_fx_rates(
        run.scenario.fx,
        module_rng(run.seed, "fx"),
        run.scenario.actuals_from,
        run.scenario.as_of,
        apply_overrides=FX_EVENT in run.profile.events,
    )
    fx.write_fx_rates(conn, rates)

    return counts | {
        "fiscal_period": len(periods),
        "period_status": len(statuses),
        "fx_rate": len(rates),
    }
