"""RLS-scoped transactions for tool queries (SPEC §7.10, §13.6)."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, text

TOOL_READER_ROLE = "vc_tool_reader"
DEFAULT_STATEMENT_TIMEOUT = "5s"
COMPANY_SCOPE_SETTING = "app.company_scope"

_COMPANY_CODE = re.compile(r"^[A-Z]{2}\d{2}$")


def _scope_value(company_scope: Sequence[str]) -> str:
    if isinstance(company_scope, str):
        raise TypeError("company_scope must be a sequence of company codes, not a string")
    if not company_scope:
        raise ValueError("company_scope is empty")
    invalid = [code for code in company_scope if not _COMPANY_CODE.fullmatch(code)]
    if invalid:
        raise ValueError(f"invalid company codes in scope: {invalid}")
    return ",".join(company_scope)


@contextmanager
def tool_transaction(
    engine: Engine,
    company_scope: Sequence[str],
    *,
    statement_timeout: str = DEFAULT_STATEMENT_TIMEOUT,
) -> Iterator[Connection]:
    """Read-only transaction as vc_tool_reader, limited to the given company codes."""
    scope = _scope_value(company_scope)
    with engine.begin() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text(f"SET LOCAL ROLE {TOOL_READER_ROLE}"))
        conn.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": statement_timeout},
        )
        conn.execute(
            text("SELECT set_config(:name, :scope, true)"),
            {"name": COMPANY_SCOPE_SETTING, "scope": scope},
        )
        yield conn
