"""Documents, operational state, audit, review and ground truth (SPEC §7.10 sections 5-6).

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from alembic import op

from variance_copilot.config import get_settings
from variance_copilot.db.ddl import create_with_comments, execute_script

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


DOCUMENTS = """
CREATE TABLE doc.document (
    doc_id         varchar(32) PRIMARY KEY,             -- e.g. POL-005
    title          text        NOT NULL,
    doc_type       text        NOT NULL CHECK (doc_type IN (
                       'POLICY','PROCEDURE','PLANNING_MEMO','COMMENTARY_EXAMPLE','REFERENCE','EXTERNAL')),
    version        text        NOT NULL,
    effective_from date        NOT NULL,
    effective_to   date,
    owner          text        NOT NULL,
    company_codes  text[],                              -- NULL = applies group-wide
    classification text        NOT NULL DEFAULT 'INTERNAL',
    source_path    text        NOT NULL,
    sha256         char(64)    NOT NULL,
    ingested_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE doc.chunk (
    chunk_id     varchar(48) PRIMARY KEY,               -- <doc_id>#<ordinal>
    doc_id       varchar(32) NOT NULL REFERENCES doc.document ON DELETE CASCADE,
    ordinal      integer     NOT NULL,
    section_path text        NOT NULL,                  -- e.g. "4 Milestones > 4.2 Capitalisation"
    content      text        NOT NULL,
    token_count  integer     NOT NULL,
    suspicious   boolean     NOT NULL DEFAULT false,    -- flagged by injection heuristics
    embedding    vector(1024),                          -- dimension fixed per embedding model (§11.5)
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', section_path || ' ' || content)) STORED,
    UNIQUE (doc_id, ordinal)
);

CREATE INDEX chunk_embedding_hnsw ON doc.chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunk_tsv_gin        ON doc.chunk USING gin (tsv);

GRANT SELECT ON doc.document, doc.chunk TO vc_tool_reader;   -- section 4 ran before these existed
"""

APP_AUDIT_REVIEW_TRUTH = """
CREATE TABLE app.run (
    run_id         uuid        PRIMARY KEY,
    kind           text        NOT NULL CHECK (kind IN ('QUESTION','COMMENTARY','EVAL','MCP_SESSION')),
    requested_by   text        NOT NULL,
    company_scope  text[]      NOT NULL,
    status         text        NOT NULL CHECK (status IN (
                       'RUNNING','SUCCEEDED','FAILED_VALIDATION','FAILED','ABORTED_BUDGET')),
    prompt_version text        NOT NULL,
    model_provider text        NOT NULL,
    model_id       text        NOT NULL,
    started_at     timestamptz NOT NULL,
    finished_at    timestamptz,
    input_tokens   integer,
    output_tokens  integer,
    cost_usd       numeric(12,6),
    output         jsonb
);

CREATE TABLE app.idempotency_key (
    key          text        PRIMARY KEY,
    requested_by text        NOT NULL,
    run_id       uuid        NOT NULL REFERENCES app.run,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit.event (
    event_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id      uuid        NOT NULL,
    seq         integer     NOT NULL,
    event_type  text        NOT NULL,
    actor       text        NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    payload     jsonb       NOT NULL,
    prev_hash   char(64)    NOT NULL,
    row_hash    char(64)    NOT NULL UNIQUE,
    UNIQUE (run_id, seq)
);

CREATE TABLE audit.fact (
    run_id        uuid           NOT NULL,
    fact_id       varchar(16)    NOT NULL,
    kind          text           NOT NULL CHECK (kind IN ('AMOUNT','PERCENT','PP','QUANTITY','RATE','COUNT')),
    value         numeric(28,10) NOT NULL,
    unit          varchar(8)     NOT NULL,                -- USD, EUR, CHF, %, pp, PK, FTE, ...
    label         text           NOT NULL,
    provenance    jsonb          NOT NULL,
    registered_at timestamptz    NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (run_id, fact_id)
);

CREATE FUNCTION audit.forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit tables are append-only (% on %)', TG_OP, TG_TABLE_NAME;
END;
$$;

CREATE TRIGGER event_append_only BEFORE UPDATE OR DELETE ON audit.event
    FOR EACH ROW EXECUTE FUNCTION audit.forbid_mutation();
CREATE TRIGGER event_no_truncate BEFORE TRUNCATE ON audit.event
    FOR EACH STATEMENT EXECUTE FUNCTION audit.forbid_mutation();
CREATE TRIGGER fact_append_only BEFORE UPDATE OR DELETE ON audit.fact
    FOR EACH ROW EXECUTE FUNCTION audit.forbid_mutation();
CREATE TRIGGER fact_no_truncate BEFORE TRUNCATE ON audit.fact
    FOR EACH STATEMENT EXECUTE FUNCTION audit.forbid_mutation();

GRANT USAGE ON SCHEMA audit TO vc_app, vc_auditor;
GRANT INSERT, SELECT ON audit.event, audit.fact TO vc_app;
GRANT SELECT ON audit.event, audit.fact TO vc_auditor;

GRANT USAGE ON SCHEMA app, review TO vc_app;
GRANT SELECT, INSERT, UPDATE ON app.run, app.idempotency_key TO vc_app;

CREATE TABLE review.commentary (
    commentary_id    uuid        PRIMARY KEY,
    run_id           uuid        NOT NULL REFERENCES app.run,
    company_scope    text[]      NOT NULL,
    fiscal_year      smallint    NOT NULL,
    period           smallint    NOT NULL,
    period_view      text        NOT NULL CHECK (period_view IN ('MTD','QTD','YTD')),
    comparison       varchar(10) NOT NULL,
    status           text        NOT NULL CHECK (status IN (
                         'DRAFT','IN_REVIEW','APPROVED','REJECTED','SUPERSEDED')),
    current_revision integer     NOT NULL,
    created_by       text        NOT NULL,
    created_at       timestamptz NOT NULL,
    approved_by      text,
    approved_at      timestamptz,
    CHECK (approved_by IS NULL OR approved_by <> created_by)
);

CREATE TABLE review.commentary_revision (
    commentary_id uuid        NOT NULL REFERENCES review.commentary,
    revision      integer     NOT NULL,
    content       jsonb       NOT NULL,       -- structured commentary with fact placeholders
    rendered_md   text        NOT NULL,
    edited_by     text        NOT NULL,
    edited_at     timestamptz NOT NULL,
    edit_reason   text,
    validation    jsonb       NOT NULL,
    PRIMARY KEY (commentary_id, revision)
);

GRANT SELECT, INSERT, UPDATE ON review.commentary, review.commentary_revision TO vc_app;

CREATE TABLE truth.scenario_event (
    event_id                varchar(8)    PRIMARY KEY,
    event_type              text          NOT NULL,
    title                   text          NOT NULL,
    company_codes           text[]        NOT NULL,
    gl_accounts             text[]        NOT NULL,
    brands                  text[],
    fiscal_year             smallint      NOT NULL,
    periods                 smallint[]    NOT NULL,
    expected_classification text          NOT NULL,
    recurrence              text          NOT NULL CHECK (recurrence IN ('RECURRING','NON_RECURRING','TIMING')),
    impact_by_version       jsonb         NOT NULL,  -- {"BUD26": -210400000.00, "FC226": -9800000.00}
    documents               text[]        NOT NULL,  -- "<company_code>/<fiscal_year>/<document_number>"
    narrative               text          NOT NULL
);

REVOKE ALL ON SCHEMA truth FROM PUBLIC;
GRANT USAGE ON SCHEMA truth TO vc_eval;
GRANT SELECT ON ALL TABLES IN SCHEMA truth TO vc_eval;
"""


def upgrade() -> None:
    bind = op.get_bind()
    dim = get_settings().embedding_dim
    create_with_comments(bind, DOCUMENTS.replace("vector(1024)", f"vector({dim})"))
    create_with_comments(bind, APP_AUDIT_REVIEW_TRUTH)


def downgrade() -> None:
    execute_script(
        op.get_bind(),
        """
        DROP TABLE truth.scenario_event;
        DROP TABLE review.commentary_revision;
        DROP TABLE review.commentary;
        DROP TABLE audit.fact;
        DROP TABLE audit.event;
        DROP FUNCTION audit.forbid_mutation();
        DROP TABLE app.idempotency_key;
        DROP TABLE app.run;
        DROP TABLE doc.chunk;
        DROP TABLE doc.document;
        REVOKE ALL ON SCHEMA app, review, audit FROM vc_app;
        REVOKE ALL ON SCHEMA audit FROM vc_auditor;
        REVOKE ALL ON SCHEMA truth FROM vc_eval;
        """,
    )
