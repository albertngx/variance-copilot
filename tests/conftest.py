"""Shared test configuration: layer markers, Hypothesis profiles and database fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from hypothesis import HealthCheck, settings

from variance_copilot.db.migrate import upgrade

LAYERS = ("unit", "property", "integration", "contract", "api", "security")
TESTS_DIR = Path(__file__).parent

# `ci` is deterministic: same examples on every run, no example database.
settings.register_profile(
    "ci",
    derandomize=True,
    database=None,
    deadline=None,
    max_examples=200,
    print_blob=True,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile("dev", max_examples=50)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test with its layer, taken from its folder under tests/."""
    for item in items:
        layer = item.path.relative_to(TESTS_DIR).parts[0]
        if layer in LAYERS:
            item.add_marker(getattr(pytest.mark, layer))


# --- Database (integration and security layers) ---------------------------

PGVECTOR_IMAGE = "pgvector/pgvector:0.8.7-pg16"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """VC_TEST_DATABASE_URL (CI service container) or a Testcontainers instance."""
    url = os.environ.get("VC_TEST_DATABASE_URL")
    if url:
        yield url
        return

    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer(PGVECTOR_IMAGE, driver=None) as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def migrated_url(database_url: str) -> str:
    upgrade(database_url)
    return database_url


@pytest.fixture
def db(migrated_url: str) -> Iterator[psycopg.Connection]:
    """A connection whose transaction is rolled back after the test."""
    with psycopg.connect(migrated_url) as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def master_data(db: psycopg.Connection) -> psycopg.Connection:
    """One valid row per md table, for company codes ES10 and US10."""
    db.execute(MASTER_DATA_SQL)
    return db


@pytest.fixture
def fin_data(master_data: psycopg.Connection) -> psycopg.Connection:
    """Balanced revenue and expense documents, plan lines and FTEs for ES10 and US10."""
    master_data.execute(FIN_DATA_SQL)
    return master_data


MASTER_DATA_SQL = """
INSERT INTO md.currency VALUES ('USD', 'US dollar', 2), ('EUR', 'Euro', 2);
INSERT INTO md.controlling_area VALUES ('A000', 'Aurelion', 'USD');
INSERT INTO md.company_code VALUES
    ('ES10', 'Test Farma S.A.U.', 'Barcelona', 'ES', 'EUR', 'AUR1', 'A000', 'ES10',
     ARRAY['SALES_AFFILIATE', 'SHARED_SERVICES']),
    ('US10', 'Test Pharmaceuticals Inc.', 'East Hanover', 'US', 'USD', 'AUR1', 'A000', 'US10',
     ARRAY['SALES_AFFILIATE']);
INSERT INTO md.segment VALUES ('IM', 'Innovative Medicines');
INSERT INTO md.functional_area VALUES
    ('F010', 'Revenue', 'REVENUE', 10),
    ('F200', 'Selling and marketing', 'SELLING_MARKETING', 30);
INSERT INTO md.profit_center
    (profit_center, controlling_area, name, segment, therapeutic_area, valid_from) VALUES
    ('ES10IMM', 'A000', 'ES immunology', 'IM', 'IMM', '2025-01-01'),
    ('US10CV', 'A000', 'US cardiovascular', 'IM', 'CV', '2025-01-01');
INSERT INTO md.cost_center
    (cost_center, controlling_area, company_code, profit_center, functional_area, category,
     name, valid_from) VALUES
    ('ES10-MKT-IMM', 'A000', 'ES10', 'ES10IMM', 'F200', 'MARKETING', 'ES IMM marketing',
     '2025-01-01'),
    ('US10-MKT-CV', 'A000', 'US10', 'US10CV', 'F200', 'MARKETING', 'US CV marketing',
     '2025-01-01');
INSERT INTO md.gl_account VALUES
    ('AUR1', '40000000', 'Gross sales - third party', 'P', 'GROSS_SALES', 'F010', 'C', NULL, false),
    ('AUR1', '61000000', 'Promotional and marketing services', 'P', 'OPEX', NULL, 'D', NULL, false),
    ('AUR1', '11000000', 'Trade receivables - third party', 'X', NULL, NULL, 'D', NULL, false),
    ('AUR1', '20000000', 'Trade payables - third party', 'X', NULL, NULL, 'C', NULL, false);
INSERT INTO md.brand VALUES ('CARDIVEX', 'Cardiovascular', 'SMALL_MOLECULE', 'MATURE');
INSERT INTO md.market_exclusivity VALUES ('CARDIVEX', 'US', '2026-05-01', 'COMPOSITION_PATENT');
INSERT INTO md.material VALUES ('CDX-100MG-30', 'CARDIVEX', 'Cardivex 100 mg', '100MG', 30, 'PK');
INSERT INTO md.customer VALUES ('C1000', 'Test Wholesale Inc.', 'US', 'WHOLESALER', NULL);
INSERT INTO md.vendor VALUES ('V1000', 'Test Agency S.L.', 'ES', 'MARKETING_AGENCY', NULL);
INSERT INTO md.wbs_element VALUES
    ('RD-TEST-01', 'Test trial', 'US10', 'US10-MKT-CV', 'CARDIVEX', 'CLINICAL_TRIAL', 'P3');
INSERT INTO md.fiscal_period VALUES (2026, 1, '2026-01-01', '2026-01-31', false);
INSERT INTO md.fx_rate VALUES ('AVG', 'EUR', 'USD', '2026-01-01', 1.12);
"""

FIN_DATA_SQL = """
INSERT INTO fin.period_status VALUES
    ('ES10', 2026, 1, 'CLOSED', '2026-02-05 18:00+01'),
    ('US10', 2026, 1, 'CLOSED', '2026-02-05 18:00-05');
INSERT INTO fin.plan_version VALUES
    ('BUD26', 2026, 'BUDGET', 'Budget 2026', 0, 'B26', '2025-11-28 12:00+00');

INSERT INTO fin.journal_header
    (company_code, fiscal_year, document_number, document_type, posting_date, document_date,
     period, entry_ts, created_by, transaction_currency) VALUES
    ('ES10', 2026, '0090000001', 'RV', '2026-01-15', '2026-01-15', 1, '2026-01-15 10:00+01',
     'BATCH_BILLING', 'EUR'),
    ('ES10', 2026, '0100000001', 'KR', '2026-01-20', '2026-01-18', 1, '2026-01-20 10:00+01',
     'INTERFACE_AP', 'EUR'),
    ('US10', 2026, '0090000001', 'RV', '2026-01-15', '2026-01-15', 1, '2026-01-15 10:00-05',
     'BATCH_BILLING', 'USD');

INSERT INTO fin.journal_line
    (company_code, fiscal_year, document_number, line_item, period, posting_date,
     chart_of_accounts, gl_account, debit_credit, amount_tc, currency_tc, amount_lc, currency_lc,
     amount_gc, currency_gc, cost_center, profit_center, functional_area, material, customer,
     vendor) VALUES
    ('ES10', 2026, '0090000001', 1, 1, '2026-01-15', 'AUR1', '11000000', 'S',
     100, 'EUR', 100, 'EUR', 112, 'USD', NULL, 'ES10IMM', NULL, NULL, NULL, NULL),
    ('ES10', 2026, '0090000001', 2, 1, '2026-01-15', 'AUR1', '40000000', 'H',
     -100, 'EUR', -100, 'EUR', -112, 'USD', NULL, 'ES10IMM', 'F010', 'CDX-100MG-30', NULL, NULL),
    ('ES10', 2026, '0100000001', 1, 1, '2026-01-20', 'AUR1', '61000000', 'S',
     50, 'EUR', 50, 'EUR', 56, 'USD', 'ES10-MKT-IMM', 'ES10IMM', 'F200', NULL, NULL, 'V1000'),
    ('ES10', 2026, '0100000001', 2, 1, '2026-01-20', 'AUR1', '20000000', 'H',
     -50, 'EUR', -50, 'EUR', -56, 'USD', NULL, NULL, NULL, NULL, NULL, 'V1000'),
    ('US10', 2026, '0090000001', 1, 1, '2026-01-15', 'AUR1', '11000000', 'S',
     300, 'USD', 300, 'USD', 300, 'USD', NULL, 'US10CV', NULL, NULL, 'C1000', NULL),
    ('US10', 2026, '0090000001', 2, 1, '2026-01-15', 'AUR1', '40000000', 'H',
     -300, 'USD', -300, 'USD', -300, 'USD', NULL, 'US10CV', 'F010', 'CDX-100MG-30', 'C1000', NULL);

INSERT INTO fin.plan_line
    (plan_category, company_code, fiscal_year, period, chart_of_accounts, gl_account,
     amount_lc, currency_lc, amount_gc, currency_gc) VALUES
    ('BUD26', 'ES10', 2026, 1, 'AUR1', '40000000', -90, 'EUR', -100.80, 'USD'),
    ('BUD26', 'US10', 2026, 1, 'AUR1', '40000000', -280, 'USD', -280, 'USD');

INSERT INTO fin.stat_key_figure VALUES
    ('ACT', 'ES10', 2026, 1, 'ES10-MKT-IMM', 'FTE', 12.5),
    ('ACT', 'US10', 2026, 1, 'US10-MKT-CV', 'FTE', 20.0);
"""
