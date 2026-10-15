"""`vc seed`: generate, load, validate and export (SPEC §8.3 steps 9-11, §8.11, §8.13)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import psycopg
from sqlalchemy import Connection, text

from variance_copilot import __version__
from variance_copilot.generator import events
from variance_copilot.generator.db import upsert
from variance_copilot.generator.master import TABLES as MASTER_TABLES
from variance_copilot.generator.model import Master, Rates
from variance_copilot.generator.pipeline import (
    Ledger,
    Run,
    all_plan_rows,
    build_ledger,
    last_closed_period,
    load_ledger,
    load_reference_data,
    prepare,
)
from variance_copilot.generator.plan import PlanContext
from variance_copilot.generator.truth import (
    EventTruth,
    Frames,
    affected_documents,
    difference,
    event_truth,
    frames,
)
from variance_copilot.generator.validate import CheckResult, Expectations, failures, run_checks

DEFAULT_OUTPUT = Path("generated")
GENERATED_TABLES = (
    "fin.journal_line", "fin.journal_header", "fin.plan_line", "fin.stat_key_figure",
    "fin.period_status", "fin.plan_version", "md.fx_rate", "md.fiscal_period",
    "truth.scenario_event",
)  # fmt: skip
EXPORTED_TABLES = (
    *(f"md.{table}" for table in MASTER_TABLES), "md.fiscal_period", "md.fx_rate",
    "fin.period_status", "fin.journal_header", "fin.journal_line", "fin.plan_version",
    "fin.plan_line", "fin.stat_key_figure", "truth.scenario_event",
)  # fmt: skip


class SeedValidationError(Exception):
    def __init__(self, results: list[CheckResult]) -> None:
        self.results = results
        super().__init__(", ".join(r.check_id for r in failures(results)))


@dataclass
class Generated:
    ledger: Ledger
    full: Frames
    without_event: dict[str, Frames]
    truths: dict[str, EventTruth]


@dataclass
class SeedResult:
    run: Run
    counts: dict[str, int]
    checks: list[CheckResult]
    manifest: dict[str, Any]
    truths: dict[str, EventTruth]


def ledger_frames(ledger: Ledger) -> Frames:
    return frames(ledger.journal.lines, all_plan_rows(ledger), Master(ledger.master))


def _decoy_details(run: Run, ledger: Ledger) -> list[dict[str, Any]]:
    event = run.scenario.event(events.DECOY_EVENT)
    budget = next(
        b for b in ledger.budgets if b.fiscal_year == events.event_year(run.scenario, event)
    )
    plan = PlanContext(
        run.scenario, Master(ledger.master), Rates(ledger.fx_rates), run.profile.company_codes
    )
    last_closed = last_closed_period(run, budget.fiscal_year)
    return [vars(d) for d in events.decoys(event, plan, budget, run.seed, last_closed)]  # type: ignore[arg-type]


def generate(run: Run) -> Generated:
    """The ledger plus one run without each event, giving each event's isolated impact."""
    ledger = build_ledger(run)
    master = Master(ledger.master)
    full = ledger_frames(ledger)
    without, truths = {}, {}
    for event_id in ledger.events:
        without[event_id] = ledger_frames(build_ledger(run, exclude={event_id}))
        diff = difference(full.actual, without[event_id].actual)
        scope = events.measure(run.scenario, event_id)
        documents = ledger.journal.event_documents.get(event_id) or affected_documents(
            ledger.journal, diff, scope, master
        )
        year = events.event_year(run.scenario, run.scenario.event(event_id))
        details = _decoy_details(run, ledger) if event_id == events.DECOY_EVENT else None
        truths[event_id] = event_truth(
            run.scenario, event_id, full, without[event_id], documents, year,
            last_closed_period(run, year), details,
        )  # fmt: skip
    return Generated(ledger, full, without, truths)


def write_truth(conn: Connection, truths: dict[str, EventTruth], ledger: Ledger) -> None:
    rows = [
        {
            "event_id": t.event_id,
            "event_type": t.event_type,
            "title": t.title,
            "company_codes": t.company_codes,
            "gl_accounts": t.gl_accounts,
            "brands": t.brands or None,
            "fiscal_year": t.fiscal_year,
            "periods": t.periods,
            "expected_classification": t.classification,
            "recurrence": t.recurrence,
            "impact_by_version": json.dumps(t.impact_by_version()),
            "documents": t.documents,
            "narrative": t.title,
        }
        for t in truths.values()
    ]
    if rows:
        upsert(conn, "truth.scenario_event", rows, ("event_id",))
        conn.execute(
            text("UPDATE truth.scenario_event SET impact_by_version = impact_by_version::jsonb")
        )


def reset(conn: Connection) -> None:
    conn.execute(text(f"TRUNCATE {', '.join(GENERATED_TABLES)} RESTART IDENTITY"))


def load(conn: Connection, run: Run, generated: Generated) -> dict[str, int]:
    reset(conn)
    counts = load_reference_data(conn, run)
    counts |= load_ledger(conn, run, generated.ledger)
    write_truth(conn, generated.truths, generated.ledger)
    return counts


def table_checksums(conn: Connection) -> tuple[dict[str, str], dict[str, int]]:
    """SHA-256 and row count of each exported table as CSV in a fixed row order."""
    conn.execute(text("SET LOCAL TimeZone TO 'UTC'"))
    raw = cast(psycopg.Connection[Any], conn.connection.dbapi_connection)
    digests, counts = {}, {}
    with raw.cursor() as cursor:
        for table in EXPORTED_TABLES:
            digest, rows = hashlib.sha256(), 0
            ordered = f'SELECT * FROM {table} t ORDER BY t::text COLLATE "C"'  # noqa: S608
            with cursor.copy(f"COPY ({ordered}) TO STDOUT (FORMAT csv)") as copy:
                for chunk in copy:
                    digest.update(chunk)
                    rows += bytes(chunk).count(b"\n")
            digests[table], counts[table] = digest.hexdigest(), rows
    return digests, counts


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


def manifest(
    run: Run, ledger: Ledger, digests: dict[str, str], rows: dict[str, int]
) -> dict[str, Any]:
    return {
        "scenario": run.scenario.scenario,
        "profile": run.name,
        "seed": run.seed,
        "as_of": run.scenario.as_of.isoformat(),
        "generator_version": __version__,
        "git_commit": _git_commit(),
        "events": ledger.events,
        "events_pending": [e for e in run.profile.events if e not in ledger.events],
        "row_counts": rows,
        "sha256": digests,
    }


def _expectations(
    conn: Connection, run: Run, generated: Generated, checksums: dict[str, str]
) -> Expectations:
    calibration_year = run.scenario.drivers.revenue.calibration_year
    calibrated = calibration_year in run.profile.fiscal_years and set(
        run.profile.company_codes
    ) == set(events.ALL_COMPANIES)

    def regenerate() -> dict[str, str]:
        with conn.begin_nested() as savepoint:
            load(conn, run, generate(run))
            digests, _ = table_checksums(conn)
            savepoint.rollback()
        return digests

    return Expectations(
        master=Master(generated.ledger.master),
        calibration_year=calibration_year if calibrated else None,
        truths=generated.truths,
        without_event=generated.without_event,
        last_closed=last_closed_period(run, run.profile.end.fiscal_year),
        decoys_active=events.DECOY_EVENT in generated.ledger.events,
        checksums=checksums,
        regenerate=regenerate,
    )


def ground_truth_json(run: Run, generated: Generated) -> dict[str, Any]:
    return {
        "scenario": run.scenario.scenario,
        "profile": run.name,
        "seed": run.seed,
        "events": [t.as_json() for t in generated.truths.values()],
    }


def _write(path: Path, content: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_seed(conn: Connection, run: Run, output: Path = DEFAULT_OUTPUT) -> SeedResult:
    """Steps 1-11 in the caller's transaction; raises SeedValidationError if a check fails."""
    generated = generate(run)
    counts = load(conn, run, generated)
    digests, rows = table_checksums(conn)
    checks = run_checks(conn, _expectations(conn, run, generated, digests), run.scenario)
    if failures(checks):
        raise SeedValidationError(checks)
    content = manifest(run, generated.ledger, digests, rows)
    _write(output / "manifest.json", content)
    _write(output / "ground_truth.json", ground_truth_json(run, generated))
    return SeedResult(run, counts, checks, content, generated.truths)


def verify(conn: Connection, output: Path = DEFAULT_OUTPUT) -> list[CheckResult]:
    """Re-run V-01..V-10 against the database for the run recorded in the manifest."""
    recorded = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    truth = json.loads((output / "ground_truth.json").read_text(encoding="utf-8"))
    run = prepare(recorded["profile"], recorded["seed"])
    generated = generate(run)
    generated.truths = {e["event_id"]: EventTruth.from_json(e) for e in truth["events"]}
    return run_checks(conn, _expectations(conn, run, generated, recorded["sha256"]), run.scenario)
