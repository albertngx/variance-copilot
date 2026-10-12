from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal

import psycopg
import pytest

SCOPED_RELATIONS = (
    "fin.journal_line",
    "fin.journal_header",
    "fin.plan_line",
    "fin.stat_key_figure",
    "rpt.pnl_actual",
    "rpt.pnl_plan",
)


@contextmanager
def as_tool_reader(conn: psycopg.Connection, scope: str | None) -> Iterator[None]:
    with conn.transaction(force_rollback=True):
        conn.execute("SET LOCAL ROLE vc_tool_reader")
        if scope is not None:
            conn.execute("SELECT set_config('app.company_scope', %s, true)", [scope])
        yield


def _company_codes(conn: psycopg.Connection, relation: str) -> set[str]:
    return {row[0] for row in conn.execute(f"SELECT DISTINCT company_code FROM {relation}")}


def test_pnl_actual_presents_income_positive_and_expense_negative(
    fin_data: psycopg.Connection,
) -> None:
    rows = fin_data.execute(
        """
        SELECT gl_account, amount_lc, amount_gc, functional_area, pnl_line, brand
        FROM rpt.pnl_actual WHERE company_code = 'ES10' ORDER BY gl_account
        """
    ).fetchall()

    assert rows == [
        ("40000000", Decimal("100.00"), Decimal("112.00"), "F010", "REVENUE", "CARDIVEX"),
        ("61000000", Decimal("-50.00"), Decimal("-56.00"), "F200", "SELLING_MARKETING", None),
    ]


def test_pnl_plan_presents_income_positive(fin_data: psycopg.Connection) -> None:
    row = fin_data.execute(
        "SELECT amount_lc, functional_area FROM rpt.pnl_plan WHERE company_code = 'ES10'"
    ).fetchone()

    assert row == (Decimal("90.00"), "F010")


def test_unbalanced_documents_lists_only_broken_documents(fin_data: psycopg.Connection) -> None:
    assert fin_data.execute("SELECT * FROM rpt.unbalanced_documents").fetchall() == []

    fin_data.execute(
        "UPDATE fin.journal_line SET amount_lc = 99 WHERE company_code = 'US10' AND line_item = 1"
    )

    rows = fin_data.execute(
        "SELECT company_code, document_number, sum_lc FROM rpt.unbalanced_documents"
    ).fetchall()
    assert rows == [("US10", "0090000001", Decimal("-201.00"))]


@pytest.mark.parametrize("relation", SCOPED_RELATIONS)
def test_scope_limits_rows_through_tables_and_views(
    fin_data: psycopg.Connection, relation: str
) -> None:
    with as_tool_reader(fin_data, "ES10"):
        assert _company_codes(fin_data, relation) == {"ES10"}


@pytest.mark.parametrize("relation", SCOPED_RELATIONS)
def test_scope_with_several_company_codes(fin_data: psycopg.Connection, relation: str) -> None:
    with as_tool_reader(fin_data, "ES10,US10"):
        assert _company_codes(fin_data, relation) == {"ES10", "US10"}


@pytest.mark.parametrize("relation", SCOPED_RELATIONS)
@pytest.mark.parametrize("scope", [None, ""], ids=["missing", "empty"])
def test_no_scope_returns_no_rows(
    fin_data: psycopg.Connection, relation: str, scope: str | None
) -> None:
    with as_tool_reader(fin_data, scope):
        assert _company_codes(fin_data, relation) == set()


def test_unbalanced_documents_respects_scope(fin_data: psycopg.Connection) -> None:
    fin_data.execute(
        "UPDATE fin.journal_line SET amount_lc = 99 WHERE company_code = 'US10' AND line_item = 1"
    )

    with as_tool_reader(fin_data, "ES10"):
        assert fin_data.execute("SELECT * FROM rpt.unbalanced_documents").fetchall() == []


def test_tool_reader_sees_master_data_unscoped(master_data: psycopg.Connection) -> None:
    with as_tool_reader(master_data, None):
        assert _company_codes(master_data, "md.company_code") == {"ES10", "US10"}
