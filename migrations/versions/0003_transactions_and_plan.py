"""Transaction and plan tables (SPEC §7.10 section 2).

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from alembic import op

from variance_copilot.db.ddl import create_with_comments, execute_script

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

DDL = """
CREATE TABLE fin.period_status (                       -- cf. posting period variant (OB52)
    company_code char(4)  NOT NULL REFERENCES md.company_code,
    fiscal_year  smallint NOT NULL,
    period       smallint NOT NULL,
    status       text     NOT NULL CHECK (status IN ('OPEN','CLOSING','CLOSED')),
    closed_at    timestamptz,
    PRIMARY KEY (company_code, fiscal_year, period),
    FOREIGN KEY (fiscal_year, period) REFERENCES md.fiscal_period
);

CREATE TABLE fin.journal_header (
    company_code         char(4)     NOT NULL REFERENCES md.company_code, -- BKPF-BUKRS
    fiscal_year          smallint    NOT NULL,           -- BKPF-GJAHR
    document_number      char(10)    NOT NULL,           -- BKPF-BELNR
    document_type        char(2)     NOT NULL,           -- BKPF-BLART
    posting_date         date        NOT NULL,           -- BKPF-BUDAT
    document_date        date        NOT NULL,           -- BKPF-BLDAT
    period               smallint    NOT NULL,           -- BKPF-MONAT
    entry_ts             timestamptz NOT NULL,           -- BKPF-CPUDT + CPUTM
    created_by           text        NOT NULL,           -- BKPF-USNAM
    transaction_currency char(3)     NOT NULL REFERENCES md.currency,     -- BKPF-WAERS
    header_text          text,                           -- BKPF-BKTXT
    reference            text,                           -- BKPF-XBLNR
    reversal_document    char(10),                       -- BKPF-STBLG
    reversal_fiscal_year smallint,                       -- BKPF-STJAH
    is_reversal          boolean     NOT NULL DEFAULT false,
    source               text        NOT NULL DEFAULT 'SEED',
    PRIMARY KEY (company_code, fiscal_year, document_number)
);

CREATE TABLE fin.journal_line (
    ledger            char(2)       NOT NULL DEFAULT '0L', -- ACDOCA-RLDNR
    company_code      char(4)       NOT NULL,              -- ACDOCA-RBUKRS
    fiscal_year       smallint      NOT NULL,              -- ACDOCA-GJAHR
    document_number   char(10)      NOT NULL,              -- ACDOCA-BELNR
    line_item         integer       NOT NULL,              -- ACDOCA-DOCLN
    period            smallint      NOT NULL,              -- ACDOCA-POPER
    posting_date      date          NOT NULL,              -- ACDOCA-BUDAT
    chart_of_accounts char(4)       NOT NULL,              -- ACDOCA-KTOPL
    gl_account        varchar(10)   NOT NULL,              -- ACDOCA-RACCT
    debit_credit      char(1)       NOT NULL CHECK (debit_credit IN ('S','H')),  -- ACDOCA-DRCRK
    amount_tc         numeric(19,2) NOT NULL,              -- ACDOCA-WSL
    currency_tc       char(3)       NOT NULL,              -- ACDOCA-RWCUR
    amount_lc         numeric(19,2) NOT NULL,              -- ACDOCA-HSL
    currency_lc       char(3)       NOT NULL,              -- ACDOCA-RHCUR
    amount_gc         numeric(19,2) NOT NULL,              -- ACDOCA-KSL
    currency_gc       char(3)       NOT NULL,              -- ACDOCA-RKCUR
    quantity          numeric(19,3),                       -- ACDOCA-MSL (positive = units shipped)
    unit              varchar(3),                          -- ACDOCA-RUNIT
    cost_center       varchar(20)   REFERENCES md.cost_center,      -- ACDOCA-RCNTR
    profit_center     varchar(10)   REFERENCES md.profit_center,    -- ACDOCA-PRCTR
    functional_area   char(4)       REFERENCES md.functional_area,  -- ACDOCA-RFAREA
    segment           varchar(10)   REFERENCES md.segment,          -- ACDOCA-SEGMENT
    trading_partner   char(6),                             -- ACDOCA-RASSC
    material          varchar(18)   REFERENCES md.material,         -- ACDOCA-MATNR
    customer          varchar(10)   REFERENCES md.customer,         -- ACDOCA-KUNNR
    vendor            varchar(10)   REFERENCES md.vendor,           -- ACDOCA-LIFNR
    wbs_element       varchar(24)   REFERENCES md.wbs_element,      -- ACDOCA-PS_POSID
    assignment        varchar(18),                         -- ACDOCA-ZUONR
    line_text         text,                                -- ACDOCA-SGTXT
    PRIMARY KEY (ledger, company_code, fiscal_year, document_number, line_item),
    FOREIGN KEY (company_code, fiscal_year, document_number) REFERENCES fin.journal_header,
    FOREIGN KEY (chart_of_accounts, gl_account) REFERENCES md.gl_account,
    CHECK ((debit_credit = 'S' AND amount_lc >= 0) OR (debit_credit = 'H' AND amount_lc <= 0))
);

CREATE INDEX jl_period_account_idx ON fin.journal_line (company_code, fiscal_year, period, gl_account);
CREATE INDEX jl_cost_center_idx    ON fin.journal_line (cost_center, fiscal_year, period);
CREATE INDEX jl_material_idx       ON fin.journal_line (material, fiscal_year, period) WHERE material IS NOT NULL;
CREATE INDEX jl_trading_partner_idx ON fin.journal_line (trading_partner) WHERE trading_partner IS NOT NULL;
CREATE INDEX jl_posting_date_brin  ON fin.journal_line USING brin (posting_date);

CREATE TABLE fin.plan_version (
    plan_category          varchar(10) PRIMARY KEY,      -- ACDOCP-CATEGORY
    fiscal_year            smallint    NOT NULL,
    kind                   text        NOT NULL CHECK (kind IN ('BUDGET','FORECAST')),
    description            text        NOT NULL,
    actuals_through_period smallint    NOT NULL DEFAULT 0 CHECK (actuals_through_period BETWEEN 0 AND 12),
    fx_rate_type           varchar(4)  NOT NULL,
    approved_at            timestamptz NOT NULL
);

CREATE TABLE fin.plan_line (
    plan_line_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plan_category     varchar(10)   NOT NULL REFERENCES fin.plan_version,  -- ACDOCP-CATEGORY
    company_code      char(4)       NOT NULL REFERENCES md.company_code,   -- ACDOCP-RBUKRS
    fiscal_year       smallint      NOT NULL,                              -- ACDOCP-RYEAR
    period            smallint      NOT NULL CHECK (period BETWEEN 1 AND 12),  -- ACDOCP-POPER
    chart_of_accounts char(4)       NOT NULL,
    gl_account        varchar(10)   NOT NULL,                              -- ACDOCP-RACCT
    cost_center       varchar(20)   REFERENCES md.cost_center,             -- ACDOCP-RCNTR
    profit_center     varchar(10)   REFERENCES md.profit_center,           -- ACDOCP-PRCTR
    functional_area   char(4)       REFERENCES md.functional_area,         -- ACDOCP-RFAREA
    segment           varchar(10)   REFERENCES md.segment,                 -- ACDOCP-SEGMENT
    trading_partner   char(6),                                             -- ACDOCP-RASSC
    material          varchar(18)   REFERENCES md.material,                -- ACDOCP-MATNR
    channel           text,
    wbs_element       varchar(24)   REFERENCES md.wbs_element,
    amount_lc         numeric(19,2) NOT NULL,                              -- ACDOCP-HSL
    currency_lc       char(3)       NOT NULL REFERENCES md.currency,
    amount_gc         numeric(19,2) NOT NULL,                              -- ACDOCP-KSL
    currency_gc       char(3)       NOT NULL REFERENCES md.currency,
    quantity          numeric(19,3),                                       -- ACDOCP-MSL
    unit              varchar(3),
    FOREIGN KEY (chart_of_accounts, gl_account) REFERENCES md.gl_account
);

CREATE INDEX pl_lookup_idx ON fin.plan_line (plan_category, company_code, fiscal_year, period, gl_account);

CREATE TABLE fin.stat_key_figure (                     -- cf. CO statistical key figures
    version      varchar(10)   NOT NULL,                -- 'ACT' or a plan category
    company_code char(4)       NOT NULL REFERENCES md.company_code,
    fiscal_year  smallint      NOT NULL,
    period       smallint      NOT NULL,
    cost_center  varchar(20)   NOT NULL REFERENCES md.cost_center,
    key_figure   text          NOT NULL CHECK (key_figure IN ('FTE','HEADCOUNT')),
    value        numeric(12,2) NOT NULL,
    PRIMARY KEY (version, company_code, fiscal_year, period, cost_center, key_figure)
);
"""

TABLES = (
    "stat_key_figure",
    "plan_line",
    "plan_version",
    "journal_line",
    "journal_header",
    "period_status",
)


def upgrade() -> None:
    create_with_comments(op.get_bind(), DDL)


def downgrade() -> None:
    execute_script(op.get_bind(), "\n".join(f"DROP TABLE fin.{t};" for t in TABLES))
