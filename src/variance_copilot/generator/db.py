"""Bulk writes for the generator (runs as the schema owner, so RLS does not apply)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

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
