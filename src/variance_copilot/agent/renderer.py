"""Markdown rendering of a structured answer with fact placeholders (SPEC §13.1, Appendix C)."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from variance_copilot.agent.schemas import Answer, Driver, ScopeEcho
from variance_copilot.finance.formatting import (
    format_amount,
    format_percent,
    format_pp,
    format_quantity,
    format_rate,
)
from variance_copilot.finance.translation import GROUP_CURRENCY
from variance_copilot.tools.facts import Fact, FactKind, FactLedger

ANSWER_LABEL = "AI-generated answer"
TOKEN = re.compile(r"\{\{(?P<body>.*?)\}\}")
_BODY = re.compile(r"(?P<fact_id>F\d+-\d+)(?:\|(?P<modifier>[a-z]+))?")
_PERIOD_RANGE = re.compile(r"\b(P\d{2})-(P\d{2})\b")
_DOT = " \N{MIDDLE DOT} "


class RenderError(ValueError):
    """The answer refers to a fact, modifier or citation this run cannot render."""


class Modifier(StrEnum):
    ABS = "abs"
    PCT = "pct"
    ABSPCT = "abspct"
    PP = "pp"


ALLOWED_KINDS: dict[Modifier | None, frozenset[FactKind]] = {
    None: frozenset({FactKind.AMOUNT, FactKind.QUANTITY, FactKind.COUNT, FactKind.RATE}),
    Modifier.ABS: frozenset({FactKind.AMOUNT, FactKind.QUANTITY}),
    Modifier.PCT: frozenset({FactKind.PERCENT}),
    Modifier.ABSPCT: frozenset({FactKind.PERCENT}),
    Modifier.PP: frozenset({FactKind.PP}),
}
_IMPACT_MODIFIER = {FactKind.PERCENT: Modifier.PCT, FactKind.PP: Modifier.PP}
_ABSOLUTE = frozenset({Modifier.ABS, Modifier.ABSPCT})
_FORMATTERS: dict[FactKind, Callable[[Fact, bool], str]] = {
    FactKind.AMOUNT: lambda fact, absolute: format_amount(fact.value, fact.unit, absolute=absolute),
    FactKind.QUANTITY: lambda fact, absolute: format_quantity(
        fact.value, fact.unit, absolute=absolute
    ),
    FactKind.COUNT: lambda fact, absolute: format_quantity(fact.value, absolute=absolute),
    FactKind.RATE: lambda fact, absolute: format_rate(fact.value, absolute=absolute),
    FactKind.PERCENT: lambda fact, absolute: format_percent(fact.value, absolute=absolute),
    FactKind.PP: lambda fact, absolute: format_pp(fact.value, absolute=absolute),
}
_CURRENCY_BASIS = {"GC": GROUP_CURRENCY, "LC": "local currency", "CC": "constant currency"}
_BASIS = {"IFRS": "IFRS", "CORE": "core"}


@dataclass(frozen=True)
class Placeholder:
    fact_id: str
    modifier: Modifier | None = None

    @property
    def token(self) -> str:
        suffix = f"|{self.modifier}" if self.modifier else ""
        return f"{{{{{self.fact_id}{suffix}}}}}"


def parse_placeholder(body: str) -> Placeholder:
    """Parse the text between ``{{`` and ``}}``; anything but ``F<call>-<seq>[|modifier]`` fails."""
    match = _BODY.fullmatch(body)
    if match is None:
        raise RenderError(f"malformed placeholder {{{{{body}}}}}")
    if match["modifier"] is None:
        return Placeholder(match["fact_id"])
    try:
        return Placeholder(match["fact_id"], Modifier(match["modifier"]))
    except ValueError:
        raise RenderError(f"unknown modifier in {{{{{body}}}}}") from None


@dataclass(frozen=True)
class CitedChunk:
    doc_id: str
    section_path: str

    @property
    def label(self) -> str:
        return f"{self.doc_id} \N{SECTION SIGN}{self.section_path.rsplit('>', 1)[-1].strip()}"


@dataclass(frozen=True)
class RenderedAnswer:
    markdown: str
    placeholders: dict[str, str]


class _Renderer:
    def __init__(self, facts: FactLedger, citations: Mapping[str, CitedChunk]) -> None:
        self._facts = facts
        self._citations = citations
        self.placeholders: dict[str, str] = {}

    def text(self, text: str) -> str:
        rendered = TOKEN.sub(lambda match: self.value(parse_placeholder(match["body"])), text)
        if "{{" in rendered or "}}" in rendered:
            raise RenderError(f"unclosed placeholder in {text!r}")
        return _PERIOD_RANGE.sub("\\1\N{EN DASH}\\2", rendered)

    def value(self, placeholder: Placeholder) -> str:
        fact = self._fact(placeholder.fact_id)
        if fact.kind not in ALLOWED_KINDS[placeholder.modifier]:
            raise RenderError(f"{placeholder.token} is not allowed for a {fact.kind} fact")
        text = _FORMATTERS[fact.kind](fact, placeholder.modifier in _ABSOLUTE)
        self.placeholders[placeholder.token] = text
        return text

    def impact(self, fact_id: str) -> str:
        kind = self._fact(fact_id).kind
        return self.value(Placeholder(fact_id, _IMPACT_MODIFIER.get(kind)))

    def citations(self, chunk_ids: Iterable[str]) -> str:
        return "; ".join(self._chunk(chunk_id).label for chunk_id in chunk_ids)

    def _fact(self, fact_id: str) -> Fact:
        fact = self._facts.get(fact_id)
        if fact is None:
            raise RenderError(f"no fact {fact_id} in this run")
        return fact

    def _chunk(self, chunk_id: str) -> CitedChunk:
        chunk = self._citations.get(chunk_id)
        if chunk is None:
            raise RenderError(f"citation {chunk_id} was not retrieved in this run")
        return chunk


def render_answer(
    answer: Answer,
    facts: FactLedger,
    *,
    run_id: UUID,
    citations: Mapping[str, CitedChunk] | None = None,
) -> RenderedAnswer:
    """Markdown in the Appendix C layout, and the text each placeholder became."""
    renderer = _Renderer(facts, citations or {})
    blocks = [f"**{renderer.text(answer.headline)}**", _scope_line(answer.scope, renderer)]
    if answer.drivers:
        lines = (_driver(n, driver, renderer) for n, driver in enumerate(answer.drivers, 1))
        blocks.append("\n".join(lines))
    in_drivers = {chunk_id for driver in answer.drivers for chunk_id in driver.citation_ids}
    sources = [c for c in dict.fromkeys(answer.citation_ids) if c not in in_drivers]
    if sources:
        blocks.append(f"*Sources: {renderer.citations(sources)}*")
    blocks += [f"*Caveat: {renderer.text(caveat)}*" for caveat in answer.caveats]
    blocks.append(_footer(run_id))
    return RenderedAnswer("\n\n".join(blocks), dict(renderer.placeholders))


def _scope_line(scope: ScopeEcho, renderer: _Renderer) -> str:
    parts = [
        ", ".join(scope.company_codes),
        renderer.text(scope.period_label),
        f"vs {scope.comparison}",
        _CURRENCY_BASIS[scope.currency_basis],
        _BASIS[scope.basis],
    ]
    if scope.preliminary:
        parts.append("preliminary")
    return f"*Scope: {_DOT.join(parts)}*"


def _driver(number: int, driver: Driver, renderer: _Renderer) -> str:
    title = renderer.text(driver.title)
    impact = renderer.impact(driver.impact_fact_id)
    line = f"{number}. **{title} ({impact}).** {renderer.text(driver.narrative)}"
    if driver.citation_ids:
        line += f" *({renderer.citations(driver.citation_ids)})*"
    return line


def _footer(run_id: UUID) -> str:
    short = f"{str(run_id)[:4]}\N{HORIZONTAL ELLIPSIS}"
    label = _DOT.join([ANSWER_LABEL, f"run {short}", "all figures traceable via"])
    return f"*{label}* `GET /v1/runs/{run_id}/facts`"
