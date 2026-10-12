from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, create_engine, text

from variance_copilot.db import sqlalchemy_url
from variance_copilot.generator.pipeline import (
    build_ledger,
    load_ledger,
    load_reference_data,
    prepare,
)


@pytest.fixture(scope="module")
def loaded(migrated_url: str) -> Iterator[tuple[Connection, dict[str, int]]]:
    """The ci profile loaded once, rolled back after the module."""
    engine = create_engine(sqlalchemy_url(migrated_url))
    with engine.connect() as conn:
        transaction = conn.begin()
        run = prepare("ci")
        load_reference_data(conn, run)
        counts = load_ledger(conn, run, build_ledger(run))
        yield conn, counts
        transaction.rollback()
    engine.dispose()


def _scalar(conn: Connection, sql: str) -> object:
    return conn.execute(text(sql)).scalar_one()


def test_ledger_loads_with_all_constraints(loaded: tuple[Connection, dict[str, int]]) -> None:
    conn, counts = loaded

    assert _scalar(conn, "SELECT count(*) FROM fin.journal_line") == counts["journal_line"]
    assert _scalar(conn, "SELECT count(*) FROM fin.plan_line") == counts["plan_line"] > 0
    assert _scalar(conn, "SELECT count(*) FROM fin.stat_key_figure") == counts["stat_key_figure"]


def test_no_unbalanced_documents(loaded: tuple[Connection, dict[str, int]]) -> None:
    assert _scalar(loaded[0], "SELECT count(*) FROM rpt.unbalanced_documents") == 0


def test_actual_and_plan_views_line_up(loaded: tuple[Connection, dict[str, int]]) -> None:
    conn = loaded[0]
    rows = conn.execute(
        text(
            """
            SELECT a.pnl_line, a.amount, p.amount
            FROM (SELECT pnl_line, sum(amount_gc) AS amount FROM rpt.pnl_actual
                  WHERE trading_partner IS NULL AND period <= 9 GROUP BY 1) a
            JOIN (SELECT pnl_line, sum(amount_gc) AS amount FROM rpt.pnl_plan
                  WHERE plan_category = 'BUD26' AND trading_partner IS NULL AND period <= 9
                  GROUP BY 1) p USING (pnl_line)
            """
        )
    ).all()
    lines = {pnl_line: (actual, plan) for pnl_line, actual, plan in rows}

    assert {"REVENUE", "COGS", "SELLING_MARKETING", "RESEARCH_DEVELOPMENT", "GENERAL_ADMIN"} <= set(
        lines
    )
    for actual, plan in lines.values():
        assert abs(actual / plan - 1) < 0.1


def test_period_ten_is_open_with_early_postings_only(
    loaded: tuple[Connection, dict[str, int]],
) -> None:
    latest = _scalar(
        loaded[0], "SELECT max(posting_date) FROM fin.journal_header WHERE fiscal_year = 2026"
    )
    assert str(latest) <= "2026-10-09"
