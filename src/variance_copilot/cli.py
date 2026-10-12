"""The ``vc`` command line (SPEC §17).

Every command is a stub until its milestone lands; stubs exit with status 1 and
name the milestone that implements them.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, NoReturn

import typer

from variance_copilot import __version__

app = typer.Typer(
    name="vc",
    help="Variance Copilot: grounded explanations of actual-vs-plan variances.",
    no_args_is_help=True,
)


def _not_implemented(milestone: str) -> NoReturn:
    typer.echo(f"not implemented ({milestone})", err=True)
    raise typer.Exit(code=1)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help="Print the version."
        ),
    ] = False,
) -> None:
    """Variance Copilot command line."""


# --- db ------------------------------------------------------------------

db_app = typer.Typer(help="Database schema management.", no_args_is_help=True)
app.add_typer(db_app, name="db")


@db_app.command("migrate")
def db_migrate(
    revision: Annotated[str, typer.Option(help="Target Alembic revision.")] = "head",
) -> None:
    """Apply Alembic migrations to VC_DATABASE_URL."""
    from variance_copilot.config import ConfigError
    from variance_copilot.db import migrate
    from variance_copilot.db.engine import database_url

    try:
        url = database_url()
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    migrate.upgrade(url, revision)


# --- seed ----------------------------------------------------------------

seed_app = typer.Typer(help="Generate and load synthetic data.")
app.add_typer(seed_app, name="seed")


@seed_app.callback(invoke_without_command=True)
def seed(
    ctx: typer.Context,
    profile: Annotated[str, typer.Option(help="Data profile: full, dev or ci.")] = "dev",
    seed_value: Annotated[int, typer.Option("--seed", help="Random seed.")] = 20261010,
) -> None:
    """Generate and load data; writes manifest and ground truth."""
    if ctx.invoked_subcommand is None:
        _not_implemented("M1")


@seed_app.command("verify")
def seed_verify() -> None:
    """Re-run validation checks V-01 to V-10."""
    _not_implemented("M1")


# --- corpus and ingestion ------------------------------------------------

corpus_app = typer.Typer(help="Document corpus.", no_args_is_help=True)
app.add_typer(corpus_app, name="corpus")


@corpus_app.command("render")
def corpus_render() -> None:
    """Render planning memos and commentary examples from scenario parameters."""
    _not_implemented("M4")


@app.command()
def ingest(
    reembed: Annotated[bool, typer.Option(help="Recompute embeddings for all chunks.")] = False,
) -> None:
    """Ingest the corpus."""
    _not_implemented("M4")


# --- questions and commentary --------------------------------------------


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Question in plain language.")],
    user: Annotated[str, typer.Option(help="Development user from config/dev_users.yaml.")],
    json_output: Annotated[
        bool, typer.Option("--json", help="Print the structured answer and run ID.")
    ] = False,
) -> None:
    """Ask a question as a development user."""
    _not_implemented("M3")


commentary_app = typer.Typer(help="Month-end commentary drafts and workflow.", no_args_is_help=True)
app.add_typer(commentary_app, name="commentary")


@commentary_app.command("create")
def commentary_create(
    scope: Annotated[str, typer.Option(help="Company code or 'group'.")],
    period: Annotated[str, typer.Option(help="Fiscal period, e.g. 2026-09.")],
    view: Annotated[str, typer.Option(help="MTD, QTD or YTD.")] = "YTD",
    vs: Annotated[str, typer.Option(help="Comparison plan version, e.g. BUD26.")] = "BUD26",
) -> None:
    """Generate a commentary draft."""
    _not_implemented("M6")


@commentary_app.command("submit")
def commentary_submit(commentary_id: Annotated[str, typer.Argument(metavar="ID")]) -> None:
    """Submit a draft for review."""
    _not_implemented("M6")


@commentary_app.command("approve")
def commentary_approve(commentary_id: Annotated[str, typer.Argument(metavar="ID")]) -> None:
    """Approve a submitted commentary."""
    _not_implemented("M6")


@commentary_app.command("reject")
def commentary_reject(commentary_id: Annotated[str, typer.Argument(metavar="ID")]) -> None:
    """Reject a submitted commentary."""
    _not_implemented("M6")


@app.command()
def anomalies(
    scope: Annotated[str, typer.Option(help="Company code or 'group'.")],
    period: Annotated[str, typer.Option(help="Fiscal period, e.g. 2026-06.")],
) -> None:
    """Run anomaly rules."""
    _not_implemented("M6")


# --- evaluation ----------------------------------------------------------


class EvalMode(StrEnum):
    REPLAY = "replay"
    RECORD = "record"
    LIVE = "live"


eval_app = typer.Typer(help="Evaluation suite.", no_args_is_help=True)
app.add_typer(eval_app, name="eval")


@eval_app.command("run")
def eval_run(
    mode: Annotated[EvalMode, typer.Option(help="Provider mode.")] = EvalMode.REPLAY,
    tags: Annotated[str | None, typer.Option(help="Comma-separated case tags.")] = None,
) -> None:
    """Run the evaluation suite."""
    _not_implemented("M7")


@eval_app.command("report")
def eval_report(run: Annotated[str, typer.Argument(help="Evaluation run ID.")]) -> None:
    """Render an evaluation report."""
    _not_implemented("M7")


# --- audit and auth ------------------------------------------------------

audit_app = typer.Typer(help="Audit trail.", no_args_is_help=True)
app.add_typer(audit_app, name="audit")


@audit_app.command("verify")
def audit_verify() -> None:
    """Verify the hash chain."""
    _not_implemented("M5")


auth_app = typer.Typer(help="Authentication helpers.", no_args_is_help=True)
app.add_typer(auth_app, name="auth")


@auth_app.command("dev-token")
def auth_dev_token(
    user: Annotated[str, typer.Option(help="Development user from config/dev_users.yaml.")],
) -> None:
    """Issue a development token."""
    _not_implemented("M6")
