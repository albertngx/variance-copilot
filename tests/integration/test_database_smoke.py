from __future__ import annotations

import psycopg


def test_pgvector_extension_is_available(db: psycopg.Connection) -> None:
    row = db.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()

    assert row is not None
    major, minor, *_ = (int(part) for part in row[0].split("."))
    assert (major, minor) >= (0, 8)


def test_server_is_postgres_16(db: psycopg.Connection) -> None:
    row = db.execute("SHOW server_version_num").fetchone()

    assert row is not None
    assert int(row[0]) // 10000 == 16
