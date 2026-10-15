from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine

from variance_copilot.db import sqlalchemy_url
from variance_copilot.generator.pipeline import prepare
from variance_copilot.generator.seed import run_seed
from variance_copilot.generator.validate import failures


def test_dev_profile_passes_every_check_including_calibration(
    migrated_url: str, tmp_path: Path
) -> None:
    engine = create_engine(sqlalchemy_url(migrated_url))
    with engine.connect() as conn, conn.begin() as transaction:
        result = run_seed(conn, prepare("dev"), tmp_path)
        transaction.rollback()
    engine.dispose()

    statuses = {c.check_id: c.status for c in result.checks}
    assert failures(result.checks) == []
    assert statuses["V-07"] == statuses["V-09"] == "PASS"
    assert 50_000 <= result.counts["journal_line"] <= 70_000
    assert result.manifest["events_pending"] == []
    assert len(result.truths) == 13
