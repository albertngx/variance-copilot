"""Generator pipeline (SPEC §8.3), steps 1-5, 7 and 8 so far."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Connection, text

from variance_copilot.generator import calendar, events, fx, master
from variance_copilot.generator.actual import actual_drivers
from variance_copilot.generator.config import (
    DEFAULT_PROFILES,
    DEFAULT_SCENARIO,
    Profile,
    ScenarioConfig,
    load_profiles,
    load_scenario,
)
from variance_copilot.generator.db import copy_rows, upsert
from variance_copilot.generator.model import Drivers, Entry, Master, Rates
from variance_copilot.generator.plan import PlanContext, budget_drivers
from variance_copilot.generator.plan_lines import plan_lines, plan_versions, stat_key_figures
from variance_copilot.generator.postings import (
    HEADER_COLUMNS,
    LINE_COLUMNS,
    Journal,
    in_window,
    post,
)
from variance_copilot.generator.rng import module_rng
from variance_copilot.generator.streams import LedgerContext, base_entries, close_entries

LEDGER_TABLES = ("fin.journal_line", "fin.journal_header", "fin.plan_line", "fin.stat_key_figure")


@dataclass(frozen=True)
class Run:
    name: str
    scenario: ScenarioConfig
    profile: Profile
    seed: int


@dataclass
class Ledger:
    """Everything the generator produced, before it is written to the database."""

    master: master.MasterData
    fx_rates: list[fx.FxRate]
    events: list[str] = field(default_factory=list)
    budgets: list[Drivers] = field(default_factory=list)
    actuals: list[Drivers] = field(default_factory=list)
    plan_entries: dict[str, list[Entry]] = field(default_factory=dict)
    actual_entries: list[Entry] = field(default_factory=list)
    journal: Journal = field(default_factory=lambda: Journal([], []))


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
    return Run(
        profile_name, scenario, profiles[profile_name], scenario.seed if seed is None else seed
    )


def generate_fx(run: Run, active: Sequence[str] | None = None) -> list[fx.FxRate]:
    active = events.active_events(run.scenario, run.profile.events) if active is None else active
    return fx.generate_fx_rates(
        run.scenario.fx,
        module_rng(run.seed, "fx"),
        run.scenario.actuals_from,
        run.scenario.as_of,
        apply_overrides=events.FX_EVENT in active,
    )


def actual_periods(run: Run, year: int) -> list[int]:
    start = run.profile.start.period if year == run.profile.start.fiscal_year else 1
    end = run.profile.end.period if year == run.profile.end.fiscal_year else 12
    if year == run.scenario.as_of.year:
        end = min(end, run.scenario.as_of.month)
    return list(range(start, end + 1))


def build_ledger(
    run: Run, exclude: Iterable[str] = (), master_dir: Path = master.DEFAULT_SEEDS_DIR
) -> Ledger:
    """Steps 4, 5, 7, 8 and 9 in memory; ``exclude`` drops events for counterfactuals."""
    data = master.read_master_data(master_dir)
    active = events.active_events(run.scenario, run.profile.events, exclude)
    ledger = Ledger(data, generate_fx(run, active), active)
    lookups, rates = Master(data), Rates(ledger.fx_rates)
    companies = run.profile.company_codes
    plan_ctx = PlanContext(run.scenario, lookups, rates, companies)
    ctx = LedgerContext(run.scenario, lookups, rates, companies, run.profile.billing, run.seed)
    as_of = run.scenario.as_of
    layers = events.layers(run.scenario, plan_ctx, active)

    for year in run.profile.fiscal_years:
        budget = budget_drivers(plan_ctx, year)
        ledger.budgets.append(budget)
        entries = list(base_entries(ctx, budget))
        ledger.plan_entries[budget.version] = entries + close_entries(ctx, budget, entries)
        periods = actual_periods(run, year)
        ledger.actuals.append(
            actual_drivers(budget, run.scenario.noise, lookups, run.seed, periods, layers)
        )

    base = [e for d in ledger.actuals for e in base_entries(ctx, d)]
    base += events.entries(ctx, ledger.actuals, active)
    base = [e for e in base if in_window(e, as_of)]
    close = [e for d in ledger.actuals for e in close_entries(ctx, d, base) if in_window(e, as_of)]
    ledger.actual_entries = base + close
    ledger.journal = post(ledger.actual_entries, lookups, rates)
    return ledger


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

    rates = generate_fx(run)
    fx.write_fx_rates(conn, rates)

    return counts | {
        "fiscal_period": len(periods),
        "period_status": len(statuses),
        "fx_rate": len(rates),
    }


def load_ledger(conn: Connection, run: Run, ledger: Ledger) -> dict[str, int]:
    """Replace the fin data with the generated ledger, plans and key figures."""
    lookups, rates = Master(ledger.master), Rates(ledger.fx_rates)
    conn.execute(text(f"TRUNCATE {', '.join(LEDGER_TABLES)} RESTART IDENTITY"))
    upsert(conn, "fin.plan_version", plan_versions(run.profile.fiscal_years), ("plan_category",))
    copy_rows(conn, "fin.journal_header", HEADER_COLUMNS, ledger.journal.headers)
    copy_rows(conn, "fin.journal_line", LINE_COLUMNS, ledger.journal.lines)

    plan_rows = [
        row
        for budget in ledger.budgets
        for row in plan_lines(budget, ledger.plan_entries[budget.version], lookups, rates)
    ]
    if plan_rows:
        copy_rows(conn, "fin.plan_line", list(plan_rows[0]), plan_rows)
    fte_per_head = run.scenario.drivers.personnel.fte_per_head
    figures = [
        row
        for drivers in [*ledger.budgets, *ledger.actuals]
        for row in stat_key_figures(drivers, lookups, fte_per_head)
    ]
    if figures:
        copy_rows(conn, "fin.stat_key_figure", list(figures[0]), figures)
    return {
        "journal_header": len(ledger.journal.headers),
        "journal_line": len(ledger.journal.lines),
        "plan_line": len(plan_rows),
        "stat_key_figure": len(figures),
    }
