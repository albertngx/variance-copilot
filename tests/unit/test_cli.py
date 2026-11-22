from __future__ import annotations

import json
import re
from decimal import Decimal
from uuid import UUID

import pytest
from typer.testing import CliRunner

from variance_copilot import __version__
from variance_copilot.agent import ask as ask_module
from variance_copilot.agent.loop import RunResult
from variance_copilot.agent.providers.base import Usage
from variance_copilot.cli import app
from variance_copilot.config import ConfigError

runner = CliRunner()
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")

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
    output = ANSI_ESCAPE.sub("", result.output)  # Rich styles output on CI even without a TTY
    for group in SPEC_GROUPS:
        assert f" {group} " in output


def test_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == __version__


@pytest.mark.parametrize(
    ("args", "milestone"),
    [
        (["corpus", "render"], "M4"),
        (["ingest"], "M4"),
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


# --- vc ask ----------------------------------------------------------------

RUN_ID = UUID("3f6c0000-0000-4000-8000-000000000001")


def run_result(status: str = "SUCCEEDED", **fields: object) -> RunResult:
    values: dict[str, object] = {
        "run_id": RUN_ID,
        "status": status,
        "answer": None,
        "markdown": "**US10 net sales were USD 41.2m below budget.**",
        "violations": (),
        "error": None,
        "turns": 2,
        "tool_calls": 1,
        "usage": Usage(2000, 200),
        "cost_usd": Decimal("0.003"),
    }
    return RunResult(**(values | fields))  # type: ignore[arg-type]


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    async def fake(question: str, user: str) -> RunResult:
        calls.append((question, user))
        return run_result()

    monkeypatch.setattr(ask_module, "ask_as_dev_user", fake)
    return calls


def test_ask_prints_the_rendered_answer(asked: list[tuple[str, str]]) -> None:
    result = runner.invoke(app, ["ask", "Why?", "--user", "analyst_us"])

    assert result.exit_code == 0
    assert result.output.strip() == "**US10 net sales were USD 41.2m below budget.**"
    assert asked == [("Why?", "analyst_us")]


def test_ask_json_prints_the_structured_result_and_run_id(asked: list[tuple[str, str]]) -> None:
    result = runner.invoke(app, ["ask", "Why?", "--user", "analyst_us", "--json"])

    assert result.exit_code == 0
    printed = json.loads(result.output)
    assert (printed["run_id"], printed["status"], printed["cost_usd"]) == (
        str(RUN_ID),
        "SUCCEEDED",
        "0.003",
    )


def test_ask_exits_non_zero_when_the_run_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failed(question: str, user: str) -> RunResult:
        return run_result("ABORTED_BUDGET", markdown=None, error="cost passed the run budget")

    monkeypatch.setattr(ask_module, "ask_as_dev_user", failed)

    result = runner.invoke(app, ["ask", "Why?", "--user", "analyst_us"])

    assert result.exit_code == 1
    assert f"run {RUN_ID} ABORTED_BUDGET: cost passed the run budget" in result.output


def test_ask_reports_configuration_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unknown(question: str, user: str) -> RunResult:
        raise ConfigError("unknown development user 'bob'")

    monkeypatch.setattr(ask_module, "ask_as_dev_user", unknown)

    result = runner.invoke(app, ["ask", "Why?", "--user", "bob"])

    assert result.exit_code == 1
    assert "unknown development user 'bob'" in result.output
