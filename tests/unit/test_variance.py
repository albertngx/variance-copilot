from __future__ import annotations

from decimal import Decimal

import pytest

from variance_copilot.config import (
    DrilldownConfig,
    MaterialityConfig,
    MaterialityThreshold,
    PnlLineMateriality,
    load_materiality,
)
from variance_copilot.finance.codes import WarningCode
from variance_copilot.finance.materiality import (
    Contributor,
    ScopeLevel,
    is_material,
    pareto,
    ranked,
)
from variance_copilot.finance.variance import Magnitude, variance

D = Decimal
MILLION = D(1_000_000)


@pytest.mark.parametrize(
    ("actual", "comparison", "delta", "pct", "favorable", "magnitude"),
    [
        ("110", "100", "10", "10", True, Magnitude.UP),
        ("90", "100", "-10", "-10", False, Magnitude.DOWN),
        ("-110", "-100", "-10", "-10", False, Magnitude.UP),
        ("-90", "-100", "10", "10", True, Magnitude.DOWN),
        ("100", "100", "0", "0", False, Magnitude.FLAT),
        ("-5", "5", "-10", "-200", False, Magnitude.FLAT),
    ],
)
def test_variance_in_presentation_sign(
    actual: str, comparison: str, delta: str, pct: str, favorable: bool, magnitude: Magnitude
) -> None:
    result = variance(D(actual), D(comparison))

    assert result.delta == D(delta)
    assert result.delta_pct == D(pct)
    assert result.favorable is favorable
    assert result.magnitude is magnitude
    assert result.warnings == ()


def test_zero_comparison_has_no_percentage() -> None:
    result = variance(D("5"), D("0"))

    assert result.delta == D("5")
    assert result.delta_pct is None
    assert result.warnings == (WarningCode.NO_COMPARISON_BASE,)
    assert result.magnitude is Magnitude.UP


def test_percentage_keeps_full_precision() -> None:
    assert variance(D("2"), D("3")).delta_pct == D(-100) / D(3)


CONFIG = MaterialityConfig(
    version=1,
    currency="USD",
    pnl_line=PnlLineMateriality(
        group=MaterialityThreshold(
            absolute=10 * MILLION, relative=D("0.05"), relative_floor=2 * MILLION
        ),
        company_code=MaterialityThreshold(
            absolute=2 * MILLION, relative=D("0.05"), relative_floor=1 * MILLION
        ),
    ),
    drilldown=DrilldownConfig(
        pareto_coverage=D("0.80"), max_contributors=5, min_contributor=D(250_000)
    ),
)


@pytest.mark.parametrize(
    ("level", "delta_m", "comparison_m", "material"),
    [
        (ScopeLevel.GROUP, "10", "1000", True),
        (ScopeLevel.GROUP, "-10", "1000", True),
        (ScopeLevel.GROUP, "9.99", "1000", False),
        (ScopeLevel.GROUP, "3", "50", True),
        (ScopeLevel.GROUP, "2", "40", True),
        (ScopeLevel.GROUP, "1.99", "10", False),
        (ScopeLevel.GROUP, "3", "61", False),
        (ScopeLevel.COMPANY_CODE, "2", "1000", True),
        (ScopeLevel.COMPANY_CODE, "-1.2", "20", True),
        (ScopeLevel.COMPANY_CODE, "1.2", "30", False),
        (ScopeLevel.COMPANY_CODE, "0.9", "1", False),
        (ScopeLevel.COMPANY_CODE, "1.5", "0", False),
    ],
)
def test_materiality_rule(
    level: ScopeLevel, delta_m: str, comparison_m: str, material: bool
) -> None:
    comparison = D(comparison_m) * MILLION
    result = variance(comparison + D(delta_m) * MILLION, comparison)

    assert is_material(result, level, CONFIG) is material


def test_materiality_reads_the_configured_thresholds() -> None:
    config = load_materiality()
    result = variance(D(3) * MILLION, D(0))

    assert is_material(result, ScopeLevel.COMPANY_CODE, config)
    assert not is_material(result, ScopeLevel.GROUP, config)


def _contributors(*deltas_m: str) -> list[Contributor]:
    return [Contributor(f"CC{i:02d}", D(d) * MILLION) for i, d in enumerate(deltas_m)]


def test_ranked_by_absolute_delta_then_key() -> None:
    items = [
        Contributor("B", D(-5)),
        Contributor("A", D(5)),
        Contributor("C", D(7)),
        Contributor("D", D(1)),
    ]

    assert [c.key for c in ranked(items)] == ["C", "A", "B", "D"]


def test_pareto_stops_once_coverage_is_reached() -> None:
    result = pareto(_contributors("-5", "-3", "-1", "-0.5", "-0.5"), CONFIG.drilldown)

    assert [c.key for c in result.contributors] == ["CC00", "CC01"]
    assert result.total == D(-10) * MILLION
    assert result.explained == D(-8) * MILLION
    assert result.coverage == D("0.8")


def test_pareto_caps_the_number_of_contributors() -> None:
    result = pareto(_contributors(*["1"] * 10), CONFIG.drilldown)

    assert len(result.contributors) == 5
    assert result.coverage == D("0.5")


def test_pareto_cap_can_be_raised_by_the_caller() -> None:
    result = pareto(_contributors(*["1"] * 30), CONFIG.drilldown, max_contributors=20)

    assert len(result.contributors) == 20


def test_pareto_skips_contributors_below_the_minimum() -> None:
    result = pareto(_contributors("0.6", "0.3", "0.2", "0.2", "0.2"), CONFIG.drilldown)

    assert [c.delta for c in result.contributors] == [D("0.6") * MILLION, D("0.3") * MILLION]
    assert result.coverage == D("0.6")


def test_pareto_coverage_is_against_the_net_variance() -> None:
    result = pareto(_contributors("50", "-40"), CONFIG.drilldown)

    assert [c.key for c in result.contributors] == ["CC00"]
    assert result.coverage == D(5)


def test_pareto_with_zero_net_variance_has_no_coverage() -> None:
    result = pareto(_contributors("5", "-5"), CONFIG.drilldown)

    assert result.total == 0
    assert result.coverage is None
    assert [c.key for c in result.contributors] == ["CC00", "CC01"]


def test_pareto_of_nothing() -> None:
    result = pareto([], CONFIG.drilldown)

    assert result.contributors == ()
    assert result.coverage is None
