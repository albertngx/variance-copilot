from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, exc, text

from variance_copilot.db import sqlalchemy_url, tool_transaction


@pytest.fixture
def engine(migrated_url: str) -> Iterator[Engine]:
    """An engine with a single pooled connection, so every checkout reuses it."""
    engine = create_engine(sqlalchemy_url(migrated_url), pool_size=1, max_overflow=0)
    yield engine
    engine.dispose()


def _session_state(engine: Engine) -> tuple[Any, ...]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT pg_backend_pid(), current_user, "
                "current_setting('app.company_scope', true), current_setting('statement_timeout')"
            )
        ).one()
    return tuple(row)


def test_settings_apply_inside_the_transaction(engine: Engine) -> None:
    with tool_transaction(engine, ["ES10", "DE10"]) as conn:
        row = conn.execute(
            text(
                "SELECT current_user, current_setting('app.company_scope'), "
                "current_setting('statement_timeout'), current_setting('transaction_read_only')"
            )
        ).one()

    assert tuple(row) == ("vc_tool_reader", "ES10,DE10", "5s", "on")


def test_scope_does_not_survive_on_the_pooled_connection(engine: Engine) -> None:
    pid, login_user, _, default_timeout = _session_state(engine)

    with tool_transaction(engine, ["ES10"]):
        pass

    reused_pid, user, scope, timeout = _session_state(engine)
    assert reused_pid == pid
    assert user == login_user
    assert scope in (None, "")
    assert timeout == default_timeout


def test_scope_is_cleared_after_rollback(engine: Engine) -> None:
    with pytest.raises(exc.DBAPIError), tool_transaction(engine, ["ES10"]) as conn:
        conn.execute(text("SELECT 1 / 0"))

    _, _, scope, _ = _session_state(engine)
    assert scope in (None, "")


def test_statement_timeout_is_enforced(engine: Engine) -> None:
    with (
        pytest.raises(exc.OperationalError, match="statement timeout"),
        tool_transaction(engine, ["ES10"], statement_timeout="200ms") as conn,
    ):
        conn.execute(text("SELECT pg_sleep(1)"))


def test_default_timeout_stops_a_six_second_query(engine: Engine) -> None:
    with (
        pytest.raises(exc.OperationalError, match="statement timeout"),
        tool_transaction(engine, ["ES10"]) as conn,
    ):
        conn.execute(text("SELECT pg_sleep(6)"))


def test_transaction_is_read_only(engine: Engine) -> None:
    with (
        pytest.raises(exc.DBAPIError, match="read-only transaction"),
        tool_transaction(engine, ["ES10"]) as conn,
    ):
        conn.execute(text("CREATE TEMP TABLE t (x int)"))


def test_scope_is_sent_as_a_bind_parameter(engine: Engine) -> None:
    statements: list[str] = []

    def capture(*args: Any) -> None:
        statements.append(args[2])

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with tool_transaction(engine, ["ES10"]):
            pass
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert statements
    assert not any("ES10" in statement for statement in statements)
