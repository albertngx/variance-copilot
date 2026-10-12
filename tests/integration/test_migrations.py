from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest
from sqlalchemy.engine import make_url
from typer.testing import CliRunner

from variance_copilot.cli import app
from variance_copilot.config import get_settings
from variance_copilot.db.migrate import downgrade, upgrade
from variance_copilot.db.session import TOOL_READER_ROLE

SCHEMAS = ("md", "fin", "rpt", "doc", "app", "audit", "review", "truth")
ROLES = ("vc_app", "vc_tool_reader", "vc_auditor", "vc_eval")


@pytest.fixture
def scratch_url(database_url: str) -> Iterator[str]:
    """A fresh, empty database on the test server."""
    name = "vc_migration_roundtrip"
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {name}")
    yield make_url(database_url).set(database=name).render_as_string(hide_password=False)
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE {name} WITH (FORCE)")


def _schemas(url: str) -> set[str]:
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s)", [list(SCHEMAS)]
        ).fetchall()
    return {row[0] for row in rows}


def test_upgrade_downgrade_upgrade(scratch_url: str) -> None:
    upgrade(scratch_url)
    assert _schemas(scratch_url) == set(SCHEMAS)

    downgrade(scratch_url)
    assert _schemas(scratch_url) == set()
    with psycopg.connect(scratch_url) as conn:
        assert conn.execute("SELECT count(*) FROM alembic_version").fetchone() == (0,)

    upgrade(scratch_url)
    assert _schemas(scratch_url) == set(SCHEMAS)


def test_roles_exist_without_login(db: psycopg.Connection) -> None:
    rows = db.execute(
        "SELECT rolname, rolcanlogin FROM pg_roles WHERE rolname = ANY(%s)", [list(ROLES)]
    ).fetchall()

    assert dict(rows) == dict.fromkeys(ROLES, False)
    assert TOOL_READER_ROLE in ROLES


def test_vc_db_migrate_is_a_no_op_at_head(
    migrated_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VC_DATABASE_URL", migrated_url)
    get_settings.cache_clear()
    try:
        result = CliRunner().invoke(app, ["db", "migrate"])
    finally:
        get_settings.cache_clear()

    assert result.exit_code == 0, result.output
