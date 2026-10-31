"""Materiality and Pareto drill-down (SPEC §9.7)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from variance_copilot.config import DrilldownConfig, MaterialityConfig
from variance_copilot.finance.precision import ZERO, with_finance_context
from variance_copilot.finance.variance import Variance


class ScopeLevel(StrEnum):
    GROUP = "GROUP"
    COMPANY_CODE = "COMPANY_CODE"


@with_finance_context
def is_material(variance: Variance, level: ScopeLevel, config: MaterialityConfig) -> bool:
    """``|Δ| ≥ absolute``, or ``|Δ%| ≥ relative`` with ``|Δ| ≥ relative_floor``."""
    lines = config.pnl_line
    threshold = lines.group if level is ScopeLevel.GROUP else lines.company_code
    size = abs(variance.delta)
    relative = variance.comparison != 0 and size >= threshold.relative * abs(variance.comparison)
    return size >= threshold.absolute or (relative and size >= threshold.relative_floor)


@dataclass(frozen=True)
class Contributor:
    key: str
    delta: Decimal


@dataclass(frozen=True)
class Drilldown:
    contributors: tuple[Contributor, ...]
    total: Decimal
    explained: Decimal
    coverage: Decimal | None


def ranked(contributors: Iterable[Contributor]) -> list[Contributor]:
    """Descending ``|Δ|``, then key (SPEC §10.1 rule 5)."""
    return sorted(contributors, key=lambda c: (-abs(c.delta), c.key))


@with_finance_context
def pareto(
    contributors: Iterable[Contributor],
    config: DrilldownConfig,
    *,
    max_contributors: int | None = None,
) -> Drilldown:
    """Largest contributors until ``pareto_coverage`` of the total is explained, within the caps."""
    ordered = ranked(contributors)
    total = sum((c.delta for c in ordered), ZERO)
    limit = config.max_contributors if max_contributors is None else max_contributors
    selected: list[Contributor] = []
    explained = ZERO
    for contributor in ordered:
        if len(selected) >= limit or abs(contributor.delta) < config.min_contributor:
            break
        if total and explained / total >= config.pareto_coverage:
            break
        selected.append(contributor)
        explained += contributor.delta
    coverage = explained / total if total else None
    return Drilldown(tuple(selected), total, explained, coverage)
