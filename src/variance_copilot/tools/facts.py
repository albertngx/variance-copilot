"""Fact ledger: every number a tool returns, with an ID and provenance (SPEC §10.2)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from variance_copilot.finance.precision import with_finance_context
from variance_copilot.finance.variance import Magnitude, Variance

FACT_PLACES = Decimal("1e-10")


class FactKind(StrEnum):
    AMOUNT = "AMOUNT"
    PERCENT = "PERCENT"
    PP = "PP"
    QUANTITY = "QUANTITY"
    RATE = "RATE"
    COUNT = "COUNT"


class Provenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_call_id: str
    tool: str
    arguments_sha256: str
    row_key: str
    field: str


class Fact(BaseModel):
    model_config = ConfigDict(frozen=True)

    fact_id: Annotated[str, Field(max_length=16)]
    kind: FactKind
    value: Decimal
    unit: Annotated[str, Field(max_length=8)]
    label: str
    favorable: bool | None = None
    magnitude: Magnitude | None = None
    provenance: Provenance


class FactRef(BaseModel):
    """A pointer the model writes as a placeholder instead of the number."""

    model_config = ConfigDict(frozen=True)

    fact_id: str


def _storable(value: Decimal) -> Decimal:
    """At most ten decimals, the scale of ``audit.fact.value``."""
    exponent = value.as_tuple().exponent
    return value.quantize(FACT_PLACES) if isinstance(exponent, int) and exponent < -10 else value


def tool_call_id(call_number: int) -> str:
    return f"tc-{call_number}"


def arguments_sha256(arguments: BaseModel) -> str:
    """SHA-256 of the validated arguments as canonical JSON."""
    payload = json.dumps(
        arguments.model_dump(mode="json", by_alias=True), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class FactRecorder:
    """Registers one tool call's facts as ``F<call>-<seq>``."""

    def __init__(self, call_number: int, tool: str, arguments_digest: str) -> None:
        self._call_number = call_number
        self._tool = tool
        self._digest = arguments_digest
        self._facts: list[Fact] = []

    @property
    def tool_call_id(self) -> str:
        return tool_call_id(self._call_number)

    @property
    def facts(self) -> list[Fact]:
        return list(self._facts)

    @with_finance_context
    def add(
        self,
        kind: FactKind,
        value: Decimal,
        unit: str,
        label: str,
        *,
        row_key: str,
        field: str,
        variance: Variance | None = None,
    ) -> FactRef:
        fact = Fact(
            fact_id=f"F{self._call_number}-{len(self._facts) + 1}",
            kind=kind,
            value=_storable(value),
            unit=unit,
            label=label,
            favorable=None if variance is None else variance.favorable,
            magnitude=None if variance is None else variance.magnitude,
            provenance=Provenance(
                tool_call_id=self.tool_call_id,
                tool=self._tool,
                arguments_sha256=self._digest,
                row_key=row_key,
                field=field,
            ),
        )
        self._facts.append(fact)
        return FactRef(fact_id=fact.fact_id)


class FactLedger:
    """All facts of one run, unique by ID."""

    def __init__(self) -> None:
        self._facts: dict[str, Fact] = {}

    def add(self, facts: Iterable[Fact]) -> None:
        for fact in facts:
            if fact.fact_id in self._facts:
                raise ValueError(f"fact {fact.fact_id} is already registered in this run")
            self._facts[fact.fact_id] = fact

    def get(self, fact_id: str) -> Fact | None:
        return self._facts.get(fact_id)

    def __iter__(self) -> Iterator[Fact]:
        return iter(self._facts.values())

    def __len__(self) -> int:
        return len(self._facts)
