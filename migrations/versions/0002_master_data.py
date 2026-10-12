"""Master data tables (SPEC §7.10 section 1).

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from alembic import op

from variance_copilot.db.ddl import create_with_comments, execute_script

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

DDL = """
CREATE TABLE md.currency (
    currency_code char(3)  PRIMARY KEY,                -- TCURC-WAERS
    name          text     NOT NULL,
    decimals      smallint NOT NULL DEFAULT 2
);

CREATE TABLE md.controlling_area (
    controlling_area char(4) PRIMARY KEY,              -- TKA01-KOKRS
    name             text    NOT NULL,
    currency_code    char(3) NOT NULL REFERENCES md.currency
);

CREATE TABLE md.company_code (
    company_code      char(4) PRIMARY KEY,             -- T001-BUKRS
    legal_name        text    NOT NULL,                -- T001-BUTXT
    city              text    NOT NULL,                -- T001-ORT01
    country_code      char(2) NOT NULL,                -- T001-LAND1
    local_currency    char(3) NOT NULL REFERENCES md.currency,   -- T001-WAERS
    chart_of_accounts char(4) NOT NULL,                -- T001-KTOPL
    controlling_area  char(4) NOT NULL REFERENCES md.controlling_area,
    trading_partner   char(6) NOT NULL UNIQUE,         -- T880-RCOMP, used in ACDOCA-RASSC
    entity_roles      text[]  NOT NULL CHECK (entity_roles <@ ARRAY[
                          'PRINCIPAL','SALES_AFFILIATE','MANUFACTURING','SHARED_SERVICES']::text[])
);

CREATE TABLE md.segment (
    segment varchar(10) PRIMARY KEY,                   -- FAGL_SEGM-SEGMENT
    name    text        NOT NULL
);

CREATE TABLE md.functional_area (
    functional_area char(4)  PRIMARY KEY,              -- TFKB-FKBER
    name            text     NOT NULL,
    pnl_line        text     NOT NULL CHECK (pnl_line IN (
                        'REVENUE','COGS','SELLING_MARKETING','RESEARCH_DEVELOPMENT',
                        'GENERAL_ADMIN','OTHER_INCOME','OTHER_EXPENSE')),
    sort_order      smallint NOT NULL
);

CREATE TABLE md.profit_center (
    profit_center    varchar(10) PRIMARY KEY,          -- CEPC-PRCTR
    controlling_area char(4)     NOT NULL REFERENCES md.controlling_area,  -- CEPC-KOKRS
    name             text        NOT NULL,             -- CEPCT-KTEXT
    segment          varchar(10) NOT NULL REFERENCES md.segment,           -- CEPC-SEGMENT
    therapeutic_area text,
    valid_from       date        NOT NULL,             -- CEPC-DATAB
    valid_to         date        NOT NULL DEFAULT DATE '9999-12-31'        -- CEPC-DATBI
);

CREATE TABLE md.cost_center (
    cost_center        varchar(20) PRIMARY KEY,        -- CSKS-KOSTL
    controlling_area   char(4)     NOT NULL REFERENCES md.controlling_area, -- CSKS-KOKRS
    company_code       char(4)     NOT NULL REFERENCES md.company_code,     -- CSKS-BUKRS
    profit_center      varchar(10) NOT NULL REFERENCES md.profit_center,    -- CSKS-PRCTR
    functional_area    char(4)     NOT NULL REFERENCES md.functional_area,  -- CSKS-FUNC_AREA
    category           text        NOT NULL CHECK (category IN (
                           'PRODUCTION','QUALITY','SALES','MARKETING','MEDICAL','RESEARCH',
                           'DEVELOPMENT','ADMINISTRATION','SERVICE')),          -- cf. CSKS-KOSAR
    name               text        NOT NULL,           -- CSKT-KTEXT
    person_responsible text,                           -- CSKS-VERAK
    valid_from         date        NOT NULL,           -- CSKS-DATAB
    valid_to           date        NOT NULL DEFAULT DATE '9999-12-31'       -- CSKS-DATBI
);

CREATE TABLE md.gl_account (
    chart_of_accounts       char(4)     NOT NULL,      -- SKA1-KTOPL
    gl_account              varchar(10) NOT NULL,      -- SKA1-SAKNR
    name                    text        NOT NULL,      -- SKAT-TXT50
    account_type            char(1)     NOT NULL CHECK (account_type IN ('X','P','S')),  -- SKA1-GLACCOUNT_TYPE
    pnl_category            text        CHECK (pnl_category IN (
                                'GROSS_SALES','GTN_DEDUCTION','OTHER_REVENUE','COGS','OPEX',
                                'ALLOCATION','OTHER_OPERATING')),
    default_functional_area char(4)     REFERENCES md.functional_area,
    normal_balance          char(1)     NOT NULL CHECK (normal_balance IN ('D','C')),
    core_adjustment         text        CHECK (core_adjustment IN (
                                'AMORTIZATION','IMPAIRMENT','RESTRUCTURING','LEGAL','DIVESTMENT')),
    is_intercompany         boolean     NOT NULL DEFAULT false,
    PRIMARY KEY (chart_of_accounts, gl_account),
    CHECK ((account_type = 'X') = (pnl_category IS NULL))
);

CREATE TABLE md.brand (
    brand            varchar(20) PRIMARY KEY,
    therapeutic_area text        NOT NULL,
    modality         text        NOT NULL CHECK (modality IN ('SMALL_MOLECULE','BIOLOGIC','INHALED')),
    lifecycle_stage  text        NOT NULL CHECK (lifecycle_stage IN ('LAUNCH','GROWTH','MATURE','DECLINE'))
);

CREATE TABLE md.market_exclusivity (
    brand        varchar(20) NOT NULL REFERENCES md.brand,
    country_code char(2)     NOT NULL,
    loe_date     date        NOT NULL,
    basis        text        NOT NULL,                 -- e.g. COMPOSITION_PATENT, DATA_PROTECTION
    PRIMARY KEY (brand, country_code)
);

CREATE TABLE md.material (
    material    varchar(18) PRIMARY KEY,               -- MARA-MATNR
    brand       varchar(20) NOT NULL REFERENCES md.brand,
    description text        NOT NULL,                  -- MAKT-MAKTX
    strength    text        NOT NULL,
    pack_size   integer     NOT NULL CHECK (pack_size > 0),
    base_unit   varchar(3)  NOT NULL                   -- MARA-MEINS
);

CREATE TABLE md.customer (
    customer        varchar(10) PRIMARY KEY,           -- KNA1-KUNNR
    name            text        NOT NULL,              -- KNA1-NAME1
    country_code    char(2)     NOT NULL,              -- KNA1-LAND1
    channel         text        NOT NULL CHECK (channel IN (
                        'WHOLESALER','SPECIALTY_PHARMACY','HOSPITAL','PUBLIC_TENDER','INTERCOMPANY')),
    trading_partner char(6)                            -- KNA1-VBUND
);

CREATE TABLE md.vendor (
    vendor          varchar(10) PRIMARY KEY,           -- LFA1-LIFNR
    name            text        NOT NULL,              -- LFA1-NAME1
    country_code    char(2)     NOT NULL,              -- LFA1-LAND1
    category        text        NOT NULL CHECK (category IN (
                        'CRO','MARKETING_AGENCY','EVENTS','CONSULTING','IT','FACILITIES',
                        'LOGISTICS','LICENSOR','RAW_MATERIALS','INTERCOMPANY','OTHER')),
    trading_partner char(6)                            -- LFA1-VBUND
);

CREATE TABLE md.wbs_element (
    wbs_element  varchar(24) PRIMARY KEY,              -- PRPS-POSID
    description  text        NOT NULL,                 -- PRPS-POST1
    company_code char(4)     NOT NULL REFERENCES md.company_code,
    cost_center  varchar(20) NOT NULL REFERENCES md.cost_center,   -- responsible cost center
    brand        varchar(20) REFERENCES md.brand,
    project_type text        NOT NULL CHECK (project_type IN ('CLINICAL_TRIAL','RESEARCH','LIFECYCLE','OTHER')),
    phase        text
);

CREATE TABLE md.fiscal_period (
    fiscal_year smallint NOT NULL,
    period      smallint NOT NULL CHECK (period BETWEEN 1 AND 16),
    start_date  date     NOT NULL,
    end_date    date     NOT NULL,
    is_special  boolean  NOT NULL,                     -- periods 13-16: year-end adjustments
    PRIMARY KEY (fiscal_year, period)
);

CREATE TABLE md.fx_rate (
    rate_type     varchar(4)    NOT NULL,              -- TCURR-KURST
    from_currency char(3)       NOT NULL REFERENCES md.currency,   -- TCURR-FCURR
    to_currency   char(3)       NOT NULL REFERENCES md.currency,   -- TCURR-TCURR
    valid_from    date          NOT NULL,              -- TCURR-GDATU
    rate          numeric(18,9) NOT NULL CHECK (rate > 0),          -- TCURR-UKURS (direct, factor 1)
    PRIMARY KEY (rate_type, from_currency, to_currency, valid_from)
);
"""

TABLES = (
    "fx_rate",
    "fiscal_period",
    "wbs_element",
    "vendor",
    "customer",
    "material",
    "market_exclusivity",
    "brand",
    "gl_account",
    "cost_center",
    "profit_center",
    "functional_area",
    "segment",
    "company_code",
    "controlling_area",
    "currency",
)


def upgrade() -> None:
    create_with_comments(op.get_bind(), DDL)


def downgrade() -> None:
    execute_script(op.get_bind(), "\n".join(f"DROP TABLE md.{t};" for t in TABLES))
