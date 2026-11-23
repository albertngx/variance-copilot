"""Run records in ``app.run`` and LLM cost accounting (SPEC §7.10, §15.5)."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Protocol
from uuid import UUID

from sqlalchemy import Connection, text

from variance_copilot.agent.providers.base import Usage
from variance_copilot.config import ConfigError, ModelPrice, ModelPrices

RunKind = Literal["QUESTION", "COMMENTARY", "EVAL", "MCP_SESSION"]
RunStatus = Literal["RUNNING", "SUCCEEDED", "FAILED_VALIDATION", "FAILED", "ABORTED_BUDGET"]
MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class RunStart:
    run_id: UUID
    kind: RunKind
    requested_by: str
    company_scope: tuple[str, ...]
    prompt_version: str
    model_provider: str
    model_id: str
    started_at: datetime


@dataclass(frozen=True)
class RunEnd:
    status: RunStatus
    finished_at: datetime
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    output: dict[str, Any]


class RunStore(Protocol):
    def start(self, run: RunStart) -> None: ...

    def finish(self, run_id: UUID, end: RunEnd) -> None: ...


class PostgresRunStore:
    """Writes ``app.run`` in transactions supplied by the caller, normally as ``vc_app``."""

    def __init__(self, transaction: Callable[[], AbstractContextManager[Connection]]) -> None:
        self._transaction = transaction

    def start(self, run: RunStart) -> None:
        with self._transaction() as conn:
            conn.execute(
                text(
                    "INSERT INTO app.run (run_id, kind, requested_by, company_scope, status, "
                    "prompt_version, model_provider, model_id, started_at) VALUES (:run_id, "
                    ":kind, :requested_by, :company_scope, 'RUNNING', :prompt_version, "
                    ":model_provider, :model_id, :started_at)"
                ),
                {
                    "run_id": run.run_id,
                    "kind": run.kind,
                    "requested_by": run.requested_by,
                    "company_scope": list(run.company_scope),
                    "prompt_version": run.prompt_version,
                    "model_provider": run.model_provider,
                    "model_id": run.model_id,
                    "started_at": run.started_at,
                },
            )

    def finish(self, run_id: UUID, end: RunEnd) -> None:
        with self._transaction() as conn:
            conn.execute(
                text(
                    "UPDATE app.run SET status = :status, finished_at = :finished_at, "
                    "input_tokens = :input_tokens, output_tokens = :output_tokens, "
                    "cost_usd = :cost_usd, output = CAST(:output AS jsonb) WHERE run_id = :run_id"
                ),
                {
                    "run_id": run_id,
                    "status": end.status,
                    "finished_at": end.finished_at,
                    "input_tokens": end.input_tokens,
                    "output_tokens": end.output_tokens,
                    "cost_usd": end.cost_usd,
                    "output": json.dumps(end.output, default=str),
                },
            )


def model_price(prices: ModelPrices, model_id: str) -> ModelPrice:
    price = prices.models.get(model_id)
    if price is None:
        raise ConfigError(
            f"no price for model {model_id!r}; add it to config/model_prices.yaml (SPEC §15.5)"
        )
    return price


def cost_usd(usage: Usage, price: ModelPrice) -> Decimal:
    """USD cost of one call: tokens times the per-million rate."""
    return (
        Decimal(usage.input_tokens) * price.input_per_mtok
        + Decimal(usage.output_tokens) * price.output_per_mtok
    ) / MILLION
