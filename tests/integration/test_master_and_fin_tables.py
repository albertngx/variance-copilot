from __future__ import annotations

import psycopg
import pytest

MD_TABLES = (
    "currency", "controlling_area", "company_code", "segment", "functional_area",
    "profit_center", "cost_center", "gl_account", "brand", "market_exclusivity", "material",
    "customer", "vendor", "wbs_element", "fiscal_period", "fx_rate",
)  # fmt: skip
FIN_TABLES = (
    "period_status", "journal_header", "journal_line", "plan_version", "plan_line",
    "stat_key_figure",
)  # fmt: skip
FIN_INDEXES = (
    "jl_period_account_idx", "jl_cost_center_idx", "jl_material_idx",
    "jl_trading_partner_idx", "jl_posting_date_brin", "pl_lookup_idx",
)  # fmt: skip

MD_CHECK_VIOLATIONS = {
    "company_code role": "UPDATE md.company_code SET entity_roles = ARRAY['BANK']",
    "functional_area pnl_line": "UPDATE md.functional_area SET pnl_line = 'EBIT'",
    "cost_center category": "UPDATE md.cost_center SET category = 'LEGAL'",
    "gl_account type": "UPDATE md.gl_account SET account_type = 'Z'",
    "gl_account category": "UPDATE md.gl_account SET pnl_category = 'EBIT'",
    "gl_account balance sheet with category": (
        "UPDATE md.gl_account SET account_type = 'X' WHERE gl_account = '40000000'"
    ),
    "gl_account P&L without category": (
        "UPDATE md.gl_account SET pnl_category = NULL WHERE gl_account = '40000000'"
    ),
    "gl_account normal balance": "UPDATE md.gl_account SET normal_balance = 'X'",
    "gl_account core adjustment": "UPDATE md.gl_account SET core_adjustment = 'OTHER'",
    "brand modality": "UPDATE md.brand SET modality = 'VACCINE'",
    "brand lifecycle": "UPDATE md.brand SET lifecycle_stage = 'RETIRED'",
    "material pack size": "UPDATE md.material SET pack_size = 0",
    "customer channel": "UPDATE md.customer SET channel = 'RETAIL'",
    "vendor category": "UPDATE md.vendor SET category = 'BANK'",
    "wbs project type": "UPDATE md.wbs_element SET project_type = 'CAPEX'",
    "fiscal period": "UPDATE md.fiscal_period SET period = 17",
    "fx rate": "UPDATE md.fx_rate SET rate = 0",
}

FIN_CHECK_VIOLATIONS = {
    "period status": "UPDATE fin.period_status SET status = 'LOCKED'",
    "debit with negative amount": (
        "UPDATE fin.journal_line SET amount_lc = -1 WHERE debit_credit = 'S'"
    ),
    "credit with positive amount": (
        "UPDATE fin.journal_line SET amount_lc = 1 WHERE debit_credit = 'H'"
    ),
    "debit/credit indicator": "UPDATE fin.journal_line SET debit_credit = 'D'",
    "plan version kind": "UPDATE fin.plan_version SET kind = 'ACTUAL'",
    "actuals through period": "UPDATE fin.plan_version SET actuals_through_period = 13",
    "plan line period": "UPDATE fin.plan_line SET period = 13",
    "key figure": "UPDATE fin.stat_key_figure SET key_figure = 'SQM'",
}


def _count(conn: psycopg.Connection, table: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def test_valid_row_in_every_md_table(master_data: psycopg.Connection) -> None:
    assert {t: _count(master_data, f"md.{t}") > 0 for t in MD_TABLES} == dict.fromkeys(
        MD_TABLES, True
    )


def test_valid_row_in_every_fin_table(fin_data: psycopg.Connection) -> None:
    assert {t: _count(fin_data, f"fin.{t}") > 0 for t in FIN_TABLES} == dict.fromkeys(
        FIN_TABLES, True
    )


@pytest.mark.parametrize("statement", MD_CHECK_VIOLATIONS.values(), ids=MD_CHECK_VIOLATIONS)
def test_md_check_constraints(master_data: psycopg.Connection, statement: str) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), master_data.transaction():
        master_data.execute(statement)


@pytest.mark.parametrize("statement", FIN_CHECK_VIOLATIONS.values(), ids=FIN_CHECK_VIOLATIONS)
def test_fin_check_constraints(fin_data: psycopg.Connection, statement: str) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), fin_data.transaction():
        fin_data.execute(statement)


def test_journal_line_requires_existing_header(fin_data: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation), fin_data.transaction():
        fin_data.execute(
            "UPDATE fin.journal_line SET document_number = '0000000000' "
            "WHERE company_code = 'US10' AND line_item = 1"
        )


def test_fin_indexes_exist(db: psycopg.Connection) -> None:
    rows = db.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'fin'").fetchall()

    assert set(FIN_INDEXES) <= {row[0] for row in rows}


@pytest.mark.parametrize(
    ("table", "column", "comment"),
    [
        ("fin.journal_line", "amount_lc", "ACDOCA-HSL"),
        ("fin.journal_header", "document_number", "BKPF-BELNR"),
        ("md.company_code", "trading_partner", "T880-RCOMP, used in ACDOCA-RASSC"),
        ("md.cost_center", "category", "cf. CSKS-KOSAR"),
        ("md.fx_rate", "rate", "TCURR-UKURS (direct, factor 1)"),
    ],
)
def test_s4hana_counterparts_are_column_comments(
    db: psycopg.Connection, table: str, column: str, comment: str
) -> None:
    row = db.execute(
        """
        SELECT col_description(a.attrelid, a.attnum)
        FROM pg_attribute a
        WHERE a.attrelid = %s::regclass AND a.attname = %s
        """,
        [table, column],
    ).fetchone()

    assert row == (comment,)
