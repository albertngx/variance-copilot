"""Who is calling and within which run: principal, tool context and limits (SPEC §10.1, §10.5)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from time import monotonic
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, event

from variance_copilot.config import GROUP_SCOPE, MaterialityConfig, Role
from variance_copilot.tools.facts import FactRecorder

FINANCE_ROLES = frozenset({Role.ANALYST, Role.REVIEWER, Role.GROUP_ANALYST})
TOOL_TIMEOUT_SECONDS = 10.0
STATEMENT_TIMEOUT_SECONDS = 5.0
MAX_ROWS = 200

type TransactionFactory = Callable[[Sequence[str]], AbstractContextManager[Connection]]


class ToolTimeoutError(Exception):
    """The tool call ran out of its time budget."""


@dataclass(frozen=True)
class Principal:
    """A verified identity: roles and the company codes it may see."""

    subject: str
    roles: frozenset[Role]
    company_scope: frozenset[str]

    @classmethod
    def from_claims(
        cls,
        subject: str,
        roles: Iterable[Role],
        company_scope: Iterable[str],
        all_company_codes: Iterable[str],
    ) -> Principal:
        """Expand the ``group`` claim to every company code; drop unknown codes."""
        known = frozenset(all_company_codes)
        claims = set(company_scope)
        scope = known if GROUP_SCOPE in claims else known & claims
        return cls(subject, frozenset(roles), scope)

    @property
    def is_group_analyst(self) -> bool:
        return Role.GROUP_ANALYST in self.roles

    @property
    def has_finance_role(self) -> bool:
        return bool(self.roles & FINANCE_ROLES)


@dataclass(frozen=True)
class ToolContext:
    """Everything a tool may use besides its input; scope comes only from here."""

    principal: Principal
    run_id: UUID
    conn: Connection
    facts: FactRecorder
    materiality: MaterialityConfig

    @property
    def tool_call_id(self) -> str:
        return self.facts.tool_call_id


@contextmanager
def deadline(conn: Connection, seconds: float) -> Iterator[None]:
    """Cap each statement's timeout so all statements finish within ``seconds``."""
    ends = monotonic() + seconds

    def cap(_conn: Connection, cursor: Any, *_args: Any) -> None:
        remaining = ends - monotonic()
        if remaining <= 0:
            raise ToolTimeoutError
        milliseconds = max(1, int(min(STATEMENT_TIMEOUT_SECONDS, remaining) * 1000))
        cursor.execute("SELECT set_config('statement_timeout', %s, true)", (f"{milliseconds}ms",))

    event.listen(conn, "before_cursor_execute", cap)
    try:
        yield
    finally:
        event.remove(conn, "before_cursor_execute", cap)
