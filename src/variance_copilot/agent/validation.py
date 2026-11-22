"""Checks a final answer before it is shown (SPEC §13); the full validators arrive in M5."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError
from pydantic_core import ErrorDetails

from variance_copilot.agent.renderer import (
    ALLOWED_KINDS,
    TOKEN,
    CitedChunk,
    RenderedAnswer,
    RenderError,
    parse_placeholder,
    render_answer,
)
from variance_copilot.agent.schemas import Answer
from variance_copilot.tools.facts import FactLedger


@dataclass(frozen=True)
class CheckedAnswer:
    answer: Answer | None
    rendered: RenderedAnswer | None
    violations: tuple[str, ...]


def check_answer(
    text: str,
    facts: FactLedger,
    *,
    run_id: UUID,
    citations: Mapping[str, CitedChunk] | None = None,
) -> CheckedAnswer:
    """Parse the answer, check every placeholder and reference, then render it."""
    try:
        answer = Answer.model_validate_json(text)
    except ValidationError as exc:
        return CheckedAnswer(None, None, tuple(_schema_violation(e) for e in exc.errors()))
    chunks = citations or {}
    violations = _reference_violations(answer, facts, chunks)
    if violations:
        return CheckedAnswer(answer, None, tuple(violations))
    try:
        rendered = render_answer(answer, facts, run_id=run_id, citations=chunks)
    except RenderError as exc:
        return CheckedAnswer(answer, None, (str(exc),))
    return CheckedAnswer(answer, rendered, ())


def _schema_violation(error: ErrorDetails) -> str:
    location = ".".join(str(part) for part in error["loc"]) or "answer"
    return f"{location}: {error['msg']}"


def _texts(answer: Answer) -> Iterator[tuple[str, str]]:
    yield "headline", answer.headline
    yield "scope.period_label", answer.scope.period_label
    for n, driver in enumerate(answer.drivers):
        yield f"drivers.{n}.title", driver.title
        yield f"drivers.{n}.narrative", driver.narrative
    for n, caveat in enumerate(answer.caveats):
        yield f"caveats.{n}", caveat


def _reference_violations(
    answer: Answer, facts: FactLedger, citations: Mapping[str, CitedChunk]
) -> list[str]:
    violations = [
        violation
        for location, text in _texts(answer)
        for match in TOKEN.finditer(text)
        if (violation := _placeholder_violation(location, match["body"], facts))
    ]
    for n, driver in enumerate(answer.drivers):
        for fact_id in (driver.impact_fact_id, *driver.evidence_fact_ids):
            if facts.get(fact_id) is None:
                violations.append(f"drivers.{n}: no fact {fact_id} in this run")
    cited = [*answer.citation_ids, *(c for driver in answer.drivers for c in driver.citation_ids)]
    violations += [
        f"citation {chunk_id} was not retrieved in this run"
        for chunk_id in dict.fromkeys(cited)
        if chunk_id not in citations
    ]
    return violations


def _placeholder_violation(location: str, body: str, facts: FactLedger) -> str | None:
    try:
        placeholder = parse_placeholder(body)
    except RenderError as exc:
        return f"{location}: {exc}"
    fact = facts.get(placeholder.fact_id)
    if fact is None:
        return f"{location}: no fact {placeholder.fact_id} in this run"
    if fact.kind not in ALLOWED_KINDS[placeholder.modifier]:
        return f"{location}: {placeholder.token} is not allowed for a {fact.kind} fact"
    return None
