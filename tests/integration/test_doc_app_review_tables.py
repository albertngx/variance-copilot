from __future__ import annotations

import psycopg
import pytest

from variance_copilot.config import get_settings

RUN_SQL = """
INSERT INTO app.run (run_id, kind, requested_by, company_scope, status, prompt_version,
                     model_provider, model_id, started_at)
VALUES ('00000000-0000-0000-0000-000000000001', 'COMMENTARY', 'analyst_es', ARRAY['ES10'],
        'SUCCEEDED', 'v1', 'replay', 'replay', now())
"""

COMMENTARY_SQL = """
INSERT INTO review.commentary (commentary_id, run_id, company_scope, fiscal_year, period,
                               period_view, comparison, status, current_revision, created_by,
                               created_at, approved_by, approved_at)
VALUES ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-000000000001',
        ARRAY['ES10'], 2026, 9, 'YTD', 'BUD26', 'APPROVED', 1, 'analyst_es', now(), %s, now())
"""


def test_chunk_embedding_has_configured_dimension(db: psycopg.Connection) -> None:
    row = db.execute(
        """
        SELECT format_type(atttypid, atttypmod) FROM pg_attribute
        WHERE attrelid = 'doc.chunk'::regclass AND attname = 'embedding'
        """
    ).fetchone()

    assert row == (f"vector({get_settings().embedding_dim})",)


def test_chunk_indexes(db: psycopg.Connection) -> None:
    rows = db.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'chunk'"
    ).fetchall()
    indexes = dict(rows)

    assert "USING hnsw (embedding vector_cosine_ops)" in indexes["chunk_embedding_hnsw"]
    assert "USING gin (tsv)" in indexes["chunk_tsv_gin"]


def test_chunk_tsv_is_generated_from_section_and_content(db: psycopg.Connection) -> None:
    db.execute(
        """
        INSERT INTO doc.document (doc_id, title, doc_type, version, effective_from, owner,
                                  source_path, sha256)
        VALUES ('POL-005', 'Capitalisation', 'POLICY', '1.0', '2025-01-01', 'Group Finance',
                'corpus/policies/POL-005.md', repeat('0', 64))
        """
    )
    db.execute(
        """
        INSERT INTO doc.chunk (chunk_id, doc_id, ordinal, section_path, content, token_count)
        VALUES ('POL-005#1', 'POL-005', 1, 'Milestones', 'Upfront payments are capitalised.', 5)
        """
    )

    row = db.execute(
        "SELECT count(*) FROM doc.chunk "
        "WHERE tsv @@ to_tsquery('english', 'milestone & capitalise')"
    ).fetchone()
    assert row == (1,)


def test_commentary_rejects_self_approval(db: psycopg.Connection) -> None:
    db.execute(RUN_SQL)

    with pytest.raises(psycopg.errors.CheckViolation), db.transaction():
        db.execute(COMMENTARY_SQL, ["analyst_es"])

    db.execute(COMMENTARY_SQL, ["reviewer_es"])
