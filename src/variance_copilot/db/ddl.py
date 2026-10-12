"""Helpers for running the SQL migrations."""

from __future__ import annotations

import re

from sqlalchemy import Connection

_CREATE_TABLE = re.compile(r"^\s*CREATE TABLE\s+([a-z_]+\.[a-z_]+)\s*\((.*)$", re.IGNORECASE)
_COLUMN = re.compile(r"^\s+([a-z_][a-z0-9_]*)\s+\S", re.IGNORECASE)
_CONSTRAINT_KEYWORDS = frozenset({"primary", "foreign", "check", "unique", "constraint"})
_TABLE_END = re.compile(r"^\);")


def _trailing_comment(line: str) -> str | None:
    in_string = False
    for index, char in enumerate(line):
        if char == "'":
            in_string = not in_string
        elif not in_string and line.startswith("--", index):
            return line[index + 2 :].strip() or None
    return None


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def comment_statements(ddl: str) -> list[str]:
    """COMMENT ON statements built from the trailing ``--`` comments in CREATE TABLE blocks."""
    statements: list[str] = []
    table: str | None = None
    column: str | None = None
    comments: dict[str, list[str]] = {}

    for line in ddl.splitlines():
        if table is None:
            match = _CREATE_TABLE.match(line)
            if match:
                table, column = match.group(1), None
                note = _trailing_comment(match.group(2))
                if note:
                    statements.append(f"COMMENT ON TABLE {table} IS {_quote(note)};")
            continue
        if _TABLE_END.match(line):
            statements.extend(
                f"COMMENT ON COLUMN {table}.{name} IS {_quote('; '.join(parts))};"
                for name, parts in comments.items()
            )
            comments.clear()
            table = None
            continue
        match = _COLUMN.match(line.split("--", 1)[0])
        if match:
            name = match.group(1)
            column = None if name.lower() in _CONSTRAINT_KEYWORDS else name
        note = _trailing_comment(line)
        if note and column:
            comments.setdefault(column, []).append(note)
    return statements


def execute_script(connection: Connection, sql: str) -> None:
    """Run a multi-statement script on the raw cursor, with no placeholder parsing."""
    dbapi_connection = connection.connection.dbapi_connection
    if dbapi_connection is None:
        raise RuntimeError("connection is closed")
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(sql)
    finally:
        cursor.close()


def create_with_comments(connection: Connection, ddl: str) -> None:
    execute_script(connection, ddl)
    comments = comment_statements(ddl)
    if comments:
        execute_script(connection, "\n".join(comments))
