"""Bulk writes for the generator (runs as the schema owner, so RLS does not apply)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, cast

import psycopg
from sqlalchemy import Connection, text


def upsert(
    conn: Connection, table: str, rows: Sequence[Mapping[str, Any]], keys: Sequence[str]
) -> None:
    """Insert ``rows`` into ``table``, updating every non-key column on key conflicts."""
    if not rows:
        return
    columns = list(rows[0])
    updates = [column for column in columns if column not in keys]
    action = (
        "DO UPDATE SET " + ", ".join(f"{column} = EXCLUDED.{column}" for column in updates)
        if updates
        else "DO NOTHING"
    )
    statement = (
        f"INSERT INTO {table} ({', '.join(columns)}) "  # noqa: S608 (identifiers from code)
        f"VALUES ({', '.join(':' + column for column in columns)}) "
        f"ON CONFLICT ({', '.join(keys)}) {action}"
    )
    conn.execute(text(statement), list(rows))


def copy_rows(
    conn: Connection, table: str, columns: Sequence[str], rows: Iterable[Mapping[str, Any]]
) -> None:
    """Bulk insert with COPY inside the connection's transaction."""
    raw = cast(psycopg.Connection[Any], conn.connection.dbapi_connection)
    with (
        raw.cursor() as cursor,
        cursor.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy,
    ):
        for row in rows:
            copy.write_row([row[column] for column in columns])
