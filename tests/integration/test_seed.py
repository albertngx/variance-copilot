from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Connection, create_engine, text

from variance_copilot.db import sqlalchemy_url
from variance_copilot.generator import seed as seed_module
from variance_copilot.generator.pipeline import prepare
from variance_copilot.generator.seed import SeedResult, SeedValidationError, run_seed, verify
from variance_copilot.generator.validate import CheckResult, failures

CHECK_IDS = [f"V-{n:02d}" for n in range(1, 11)]


@pytest.fixture(scope="module")
def seeded(
    migrated_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[Connection, SeedResult, Path, float]]:
    """The ci profile seeded once inside a transaction that is rolled back afterwards."""
    engine = create_engine(sqlalchemy_url(migrated_url))
    output = tmp_path_factory.mktemp("generated")
    with engine.connect() as conn:
        transaction = conn.begin()
        started = time.monotonic()
        result = run_seed(conn, prepare("ci", 20261010), output)
        elapsed = time.monotonic() - started
        yield conn, result, output, elapsed
        transaction.rollback()
    engine.dispose()


def test_all_checks_pass_or_do_not_apply(
    seeded: tuple[Connection, SeedResult, Path, float],
) -> None:
    result = seeded[1]

    assert [c.check_id for c in result.checks] == CHECK_IDS
    assert failures(result.checks) == []
    statuses = {c.check_id: c.status for c in result.checks}
    assert statuses["V-07"] == "N/A"  # ci has no FY2025
    assert statuses["V-09"] == "N/A"  # no decoys in ci
    assert statuses["V-08"] == statuses["V-10"] == "PASS"


def test_ci_profile_is_fast_and_sized(seeded: tuple[Connection, SeedResult, Path, float]) -> None:
    _, result, _, elapsed = seeded

    assert 10_000 <= result.counts["journal_line"] <= 15_000
    assert elapsed < 60


def test_manifest_contents(seeded: tuple[Connection, SeedResult, Path, float]) -> None:
    conn, result, output, _ = seeded
    manifest = json.loads((output / "manifest.json").read_text())

    assert manifest == result.manifest
    assert {"seed", "profile", "generator_version", "git_commit", "row_counts", "sha256"} <= set(
        manifest
    )
    assert manifest["profile"] == "ci"
    assert manifest["seed"] == 20261010
    assert manifest["row_counts"]["fin.journal_line"] == result.counts["journal_line"]
    assert all(len(digest) == 64 for digest in manifest["sha256"].values())
    count = conn.execute(text("SELECT count(*) FROM fin.journal_header")).scalar_one()
    assert manifest["row_counts"]["fin.journal_header"] == count


def test_ground_truth_and_truth_table(seeded: tuple[Connection, SeedResult, Path, float]) -> None:
    conn, result, output, _ = seeded
    truth = json.loads((output / "ground_truth.json").read_text())
    rows = conn.execute(
        text("SELECT event_id, impact_by_version, documents FROM truth.scenario_event")
    ).all()

    assert [e["event_id"] for e in truth["events"]] == ["E01", "E02", "E05", "E06", "E07"]
    assert {row.event_id for row in rows} == set(result.truths)
    for row in rows:
        assert set(row.impact_by_version) == {"BUD26", "FC126", "FC226"}
        for reference in row.documents:
            company_code, year, number = reference.split("/")
            found = conn.execute(
                text(
                    "SELECT 1 FROM fin.journal_header WHERE company_code = :c AND fiscal_year = :y"
                    " AND document_number = :n"
                ),
                {"c": company_code, "y": int(year), "n": number},
            ).one_or_none()
            assert found is not None, reference


def test_ground_truth_is_not_visible_to_tools(
    seeded: tuple[Connection, SeedResult, Path, float],
) -> None:
    conn = seeded[0]
    with conn.begin_nested() as savepoint:
        conn.execute(text("SET LOCAL ROLE vc_tool_reader"))
        with pytest.raises(Exception, match="permission denied"):
            conn.execute(text("SELECT * FROM truth.scenario_event"))
        savepoint.rollback()


def test_verify_passes_then_catches_tampering(
    seeded: tuple[Connection, SeedResult, Path, float],
) -> None:
    conn, _, output, _ = seeded
    assert failures(verify(conn, output)) == []

    with conn.begin_nested() as savepoint:
        conn.execute(
            text(
                "UPDATE fin.journal_line SET amount_lc = amount_lc + 1, amount_gc = amount_gc + 1"
                " WHERE (company_code, fiscal_year, document_number, line_item) ="
                " (SELECT company_code, fiscal_year, document_number, line_item"
                "  FROM fin.journal_line WHERE debit_credit = 'S' ORDER BY 1, 2, 3, 4 LIMIT 1)"
            )
        )
        failed = {c.check_id for c in failures(verify(conn, output))}
        savepoint.rollback()
    assert "V-01" in failed


def test_failed_check_aborts_without_writing(
    seeded: tuple[Connection, SeedResult, Path, float],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing(*_: object) -> list[CheckResult]:
        return [CheckResult("V-01", "forced", "FAIL", "1 offending rows", [{"document": "x"}])]

    monkeypatch.setattr(seed_module, "run_checks", failing)
    conn = seeded[0]
    with conn.begin_nested() as savepoint:
        with pytest.raises(SeedValidationError, match="V-01") as raised:
            run_seed(conn, prepare("ci"), tmp_path)
        savepoint.rollback()

    assert raised.value.results[0].samples == [{"document": "x"}]
    assert not (tmp_path / "manifest.json").exists()
