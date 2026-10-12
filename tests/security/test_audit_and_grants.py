from __future__ import annotations

import psycopg
import pytest

EVENT_SQL = """
INSERT INTO audit.event (run_id, seq, event_type, actor, payload, prev_hash, row_hash)
VALUES ('00000000-0000-0000-0000-000000000001', 1, 'RUN_STARTED', 'analyst_es', '{}',
        repeat('0', 64), repeat('a', 64))
"""

FACT_SQL = """
INSERT INTO audit.fact (run_id, fact_id, kind, value, unit, label, provenance)
VALUES ('00000000-0000-0000-0000-000000000001', 'F1-1', 'AMOUNT', 1, 'USD', 'Net sales', '{}')
"""

MUTATIONS = {
    "update event": "UPDATE audit.event SET actor = 'x'",
    "delete event": "DELETE FROM audit.event",
    "truncate event": "TRUNCATE audit.event",
    "update fact": "UPDATE audit.fact SET value = 2",
    "delete fact": "DELETE FROM audit.fact",
    "truncate fact": "TRUNCATE audit.fact",
}

PROTECTED_FROM_TOOLS = {
    "app": "SELECT * FROM app.run",
    "audit": "SELECT * FROM audit.event",
    "review": "SELECT * FROM review.commentary",
    "truth": "SELECT * FROM truth.scenario_event",
}


def _as_role(conn: psycopg.Connection, role: str, statement: str) -> None:
    with conn.transaction(force_rollback=True):
        conn.execute(f"SET LOCAL ROLE {role}")
        conn.execute(statement)


@pytest.mark.parametrize("statement", MUTATIONS.values(), ids=MUTATIONS)
def test_audit_tables_are_append_only(db: psycopg.Connection, statement: str) -> None:
    db.execute(EVENT_SQL)
    db.execute(FACT_SQL)

    with (
        pytest.raises(psycopg.errors.RaiseException, match="audit tables are append-only"),
        db.transaction(),
    ):
        db.execute(statement)


def test_app_role_can_append_and_read_audit(db: psycopg.Connection) -> None:
    _as_role(db, "vc_app", EVENT_SQL)
    _as_role(db, "vc_app", FACT_SQL)
    _as_role(db, "vc_auditor", "SELECT * FROM audit.event, audit.fact")


@pytest.mark.parametrize("statement", PROTECTED_FROM_TOOLS.values(), ids=PROTECTED_FROM_TOOLS)
def test_tool_reader_cannot_read_protected_schemas(db: psycopg.Connection, statement: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _as_role(db, "vc_tool_reader", statement)


@pytest.mark.parametrize("role", ["vc_app", "vc_tool_reader", "vc_auditor"])
def test_only_eval_role_reads_ground_truth(db: psycopg.Connection, role: str) -> None:
    _as_role(db, "vc_eval", "SELECT * FROM truth.scenario_event")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _as_role(db, role, "SELECT * FROM truth.scenario_event")


def test_public_has_no_access_to_ground_truth(db: psycopg.Connection) -> None:
    row = db.execute(
        """
        SELECT count(*) FROM pg_namespace n, aclexplode(n.nspacl) acl
        WHERE n.nspname = 'truth' AND acl.grantee = 0
        """
    ).fetchone()

    assert row == (0,)


def test_tool_reader_can_read_documents(db: psycopg.Connection) -> None:
    _as_role(db, "vc_tool_reader", "SELECT * FROM doc.document, doc.chunk")
