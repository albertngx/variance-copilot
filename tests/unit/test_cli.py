from __future__ import annotations

import pytest
from typer.testing import CliRunner

from variance_copilot import __version__
from variance_copilot.cli import app

runner = CliRunner()

SPEC_GROUPS = (
    "db",
    "seed",
    "corpus",
    "ingest",
    "ask",
    "commentary",
    "anomalies",
    "eval",
    "audit",
    "auth",
)


def test_help_lists_every_command_group() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for group in SPEC_GROUPS:
        assert f" {group} " in result.output


def test_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == __version__


@pytest.mark.parametrize(
    ("args", "milestone"),
    [
        (["db", "migrate"], "M1"),
        (["seed", "--profile", "ci"], "M1"),
        (["seed", "verify"], "M1"),
        (["corpus", "render"], "M4"),
        (["ingest"], "M4"),
        (["ask", "Why?", "--user", "analyst_us"], "M3"),
        (["commentary", "create", "--scope", "US10", "--period", "2026-09"], "M6"),
        (["commentary", "approve", "42"], "M6"),
        (["anomalies", "--scope", "ES10", "--period", "2026-06"], "M6"),
        (["eval", "run", "--mode", "replay"], "M7"),
        (["eval", "report", "run-1"], "M7"),
        (["audit", "verify"], "M5"),
        (["auth", "dev-token", "--user", "analyst_us"], "M6"),
    ],
)
def test_stubs_exit_non_zero_with_milestone(args: list[str], milestone: str) -> None:
    result = runner.invoke(app, args)

    assert result.exit_code == 1
    assert f"not implemented ({milestone})" in result.output
