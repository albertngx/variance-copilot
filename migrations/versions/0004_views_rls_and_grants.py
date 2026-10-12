"""Semantic views, row-level security and grants (SPEC §7.10 sections 3-4).

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

from alembic import op

from variance_copilot.db.ddl import execute_script

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

VIEWS = """
CREATE VIEW rpt.pnl_actual WITH (security_invoker = true) AS
SELECT
    jl.company_code,
    jl.fiscal_year,
    jl.period,
    jl.posting_date,
    jl.document_number,
    jl.line_item,
    jl.gl_account,
    ga.name                                                  AS gl_account_name,
    ga.pnl_category,
    COALESCE(jl.functional_area, ga.default_functional_area) AS functional_area,
    fa.pnl_line,
    jl.profit_center,
    pc.segment,
    jl.cost_center,
    jl.material,
    m.brand,
    jl.customer,
    c.channel,
    jl.vendor,
    jl.wbs_element,
    jl.trading_partner,
    (jl.trading_partner IS NOT NULL)                         AS is_intercompany,
    ga.core_adjustment,
    jl.assignment,
    jl.line_text,
    -jl.amount_lc                                            AS amount_lc,
    jl.currency_lc,
    -jl.amount_gc                                            AS amount_gc,
    jl.currency_gc,
    jl.quantity
FROM fin.journal_line jl
JOIN md.gl_account ga
  ON ga.chart_of_accounts = jl.chart_of_accounts
 AND ga.gl_account        = jl.gl_account
LEFT JOIN md.functional_area fa
  ON fa.functional_area = COALESCE(jl.functional_area, ga.default_functional_area)
LEFT JOIN md.profit_center pc ON pc.profit_center = jl.profit_center
LEFT JOIN md.material      m  ON m.material       = jl.material
LEFT JOIN md.customer      c  ON c.customer       = jl.customer
WHERE jl.ledger = '0L'
  AND ga.account_type IN ('P','S');

CREATE VIEW rpt.pnl_plan WITH (security_invoker = true) AS
SELECT
    pl.plan_category,
    pl.company_code,
    pl.fiscal_year,
    pl.period,
    pl.gl_account,
    ga.name                                                  AS gl_account_name,
    ga.pnl_category,
    COALESCE(pl.functional_area, ga.default_functional_area) AS functional_area,
    fa.pnl_line,
    pl.profit_center,
    pl.segment,
    pl.cost_center,
    pl.material,
    m.brand,
    pl.channel,
    pl.wbs_element,
    pl.trading_partner,
    (pl.trading_partner IS NOT NULL)                         AS is_intercompany,
    ga.core_adjustment,
    -pl.amount_lc                                            AS amount_lc,
    pl.currency_lc,
    -pl.amount_gc                                            AS amount_gc,
    pl.currency_gc,
    pl.quantity
FROM fin.plan_line pl
JOIN md.gl_account ga
  ON ga.chart_of_accounts = pl.chart_of_accounts
 AND ga.gl_account        = pl.gl_account
LEFT JOIN md.functional_area fa
  ON fa.functional_area = COALESCE(pl.functional_area, ga.default_functional_area)
LEFT JOIN md.material m ON m.material = pl.material
WHERE ga.account_type IN ('P','S');

-- Integrity view: must always be empty (checked by the generator and tests)
CREATE VIEW rpt.unbalanced_documents WITH (security_invoker = true) AS
SELECT company_code, fiscal_year, document_number,
       SUM(amount_lc) AS sum_lc, SUM(amount_gc) AS sum_gc
FROM fin.journal_line
GROUP BY company_code, fiscal_year, document_number
HAVING SUM(amount_lc) <> 0 OR SUM(amount_gc) <> 0;
"""

RLS_AND_GRANTS = """
ALTER TABLE fin.journal_line    ENABLE ROW LEVEL SECURITY;
ALTER TABLE fin.journal_header  ENABLE ROW LEVEL SECURITY;
ALTER TABLE fin.plan_line       ENABLE ROW LEVEL SECURITY;
ALTER TABLE fin.stat_key_figure ENABLE ROW LEVEL SECURITY;

CREATE POLICY company_scope ON fin.journal_line FOR SELECT TO vc_tool_reader
    USING (company_code::text = ANY (string_to_array(current_setting('app.company_scope', true), ',')));
CREATE POLICY company_scope ON fin.journal_header FOR SELECT TO vc_tool_reader
    USING (company_code::text = ANY (string_to_array(current_setting('app.company_scope', true), ',')));
CREATE POLICY company_scope ON fin.plan_line FOR SELECT TO vc_tool_reader
    USING (company_code::text = ANY (string_to_array(current_setting('app.company_scope', true), ',')));
CREATE POLICY company_scope ON fin.stat_key_figure FOR SELECT TO vc_tool_reader
    USING (company_code::text = ANY (string_to_array(current_setting('app.company_scope', true), ',')));

GRANT USAGE ON SCHEMA md, fin, rpt, doc TO vc_tool_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA md, rpt, doc TO vc_tool_reader;
GRANT SELECT ON fin.journal_line, fin.journal_header, fin.plan_line, fin.plan_version,
                fin.stat_key_figure, fin.period_status TO vc_tool_reader;
"""

RLS_TABLES = ("journal_line", "journal_header", "plan_line", "stat_key_figure")


def upgrade() -> None:
    bind = op.get_bind()
    execute_script(bind, VIEWS)
    execute_script(bind, RLS_AND_GRANTS)


def downgrade() -> None:
    bind = op.get_bind()
    execute_script(
        bind,
        """
        REVOKE ALL ON ALL TABLES IN SCHEMA md, fin, rpt, doc FROM vc_tool_reader;
        REVOKE USAGE ON SCHEMA md, fin, rpt, doc FROM vc_tool_reader;
        DROP VIEW rpt.unbalanced_documents;
        DROP VIEW rpt.pnl_plan;
        DROP VIEW rpt.pnl_actual;
        """,
    )
    for table in RLS_TABLES:
        execute_script(
            bind,
            f"DROP POLICY company_scope ON fin.{table};"
            f"ALTER TABLE fin.{table} DISABLE ROW LEVEL SECURITY;",
        )
