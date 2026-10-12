from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, create_engine, text

from variance_copilot.db import sqlalchemy_url
from variance_copilot.generator.pipeline import load_reference_data, prepare

TABLES = (
    "md.currency", "md.company_code", "md.profit_center", "md.cost_center", "md.gl_account",
    "md.brand", "md.market_exclusivity", "md.material", "md.customer", "md.vendor",
    "md.wbs_element", "md.fiscal_period", "md.fx_rate", "fin.period_status",
)  # fmt: skip


@pytest.fixture
def conn(migrated_url: str) -> Iterator[Connection]:
    engine: Engine = create_engine(sqlalchemy_url(migrated_url))
    with engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()
    engine.dispose()


def _snapshot(conn: Connection) -> dict[str, list[tuple[object, ...]]]:
    return {
        table: [tuple(row) for row in conn.execute(text(f"SELECT * FROM {table} ORDER BY 1, 2, 3"))]
        for table in TABLES
    }


def test_reference_data_loads_and_reload_is_identical(conn: Connection) -> None:
    run = prepare("dev")

    counts = load_reference_data(conn, run)
    first = _snapshot(conn)
    load_reference_data(conn, run)

    assert _snapshot(conn) == first
    assert len(first["md.cost_center"]) == counts["cost_center"] == 45
    assert len(first["fin.period_status"]) == counts["period_status"] == 5 * (13 + 10)


def test_ci_profile_limits_companies_and_years(conn: Connection) -> None:
    load_reference_data(conn, prepare("ci"))

    rows = conn.execute(
        text("SELECT DISTINCT company_code, fiscal_year FROM fin.period_status ORDER BY 1")
    ).all()
    assert [tuple(row) for row in rows] == [
        ("CH10", 2026), ("ES10", 2026), ("IE10", 2026), ("US10", 2026)
    ]  # fmt: skip


def test_every_cost_center_has_a_profit_center(conn: Connection) -> None:
    load_reference_data(conn, prepare("ci"))

    row = conn.execute(
        text("SELECT count(*) FROM md.cost_center JOIN md.profit_center USING (profit_center)")
    ).one()
    assert row[0] == 45
