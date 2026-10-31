from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from variance_copilot.config import DrilldownConfig
from variance_copilot.finance.formatting import MINUS, format_amount
from variance_copilot.finance.materiality import Contributor, pareto, ranked
from variance_copilot.finance.periods import Period
from variance_copilot.finance.translation import FxRate, FxRates, LocalAmount, fx_split
from variance_copilot.finance.variance import Magnitude, variance
from variance_copilot.finance.versions import PlanKind, PlanVersion, resolve_version

VERSIONS = [
    PlanVersion("BUD26", 2026, PlanKind.BUDGET, 0, "B26", datetime(2025, 11, 28, tzinfo=UTC))
]
MONTHS = range(1, 13)

amounts = st.decimals(min_value=-(10**11), max_value=10**11, places=2)
rates = st.decimals(min_value=Decimal("0.5"), max_value=Decimal("2"), places=6)


@st.composite
def fx_rates(draw: st.DrawFn) -> FxRates:
    rows = [FxRate("B26", c, date(2026, 1, 1), draw(rates)) for c in ("CHF", "EUR")]
    rows += [
        FxRate("AVG", c, date(2025, m, 1), draw(rates)) for c in ("CHF", "EUR") for m in MONTHS
    ]
    return FxRates(rows)


local_amounts = st.builds(
    LocalAmount,
    period=st.builds(Period, st.just(2026), st.integers(1, 12)),
    currency=st.sampled_from(["CHF", "EUR", "USD"]),
    amount_lc=amounts,
    amount_gc=amounts,
)


@given(
    rows=st.lists(local_amounts, max_size=24),
    comparison_gc=amounts,
    rate_table=fx_rates(),
    requested=st.sampled_from(["BUD26", "PY"]),
)
def test_total_variance_is_operational_plus_fx(
    rows: list[LocalAmount], comparison_gc: Decimal, rate_table: FxRates, requested: str
) -> None:
    periods = sorted({r.period for r in rows}) or [Period(2026, 1)]
    comparison = resolve_version(requested, periods, VERSIONS)

    split = fx_split(rows, comparison_gc, comparison, rate_table)

    assert split.delta_total == split.delta_operational + split.fx_effect
    assert split.delta_total == split.actual_gc - comparison_gc


@given(
    values=st.lists(amounts, min_size=1, max_size=12),
    comparison_gc=amounts,
    rate_table=fx_rates(),
)
def test_usd_amounts_have_no_fx_effect(
    values: list[Decimal], comparison_gc: Decimal, rate_table: FxRates
) -> None:
    rows = [LocalAmount(Period(2026, m), "USD", v, v) for m, v in zip(MONTHS, values, strict=False)]
    comparison = resolve_version("BUD26", [r.period for r in rows], VERSIONS)

    split = fx_split(rows, comparison_gc, comparison, rate_table)

    assert split.actual_cc == split.actual_gc
    assert split.fx_effect == 0


@given(actual=amounts, comparison=amounts)
def test_variance_flags_follow_the_definitions(actual: Decimal, comparison: Decimal) -> None:
    result = variance(actual, comparison)

    assert result.delta == actual - comparison
    assert result.favorable is (result.delta > 0)
    if abs(actual) == abs(comparison):
        assert result.magnitude is Magnitude.FLAT
    else:
        assert (result.magnitude is Magnitude.UP) is (abs(actual) > abs(comparison))
    if comparison == 0:
        assert result.delta_pct is None
    else:
        assert result.delta_pct is not None
        assert (result.delta_pct > 0) is (result.delta > 0)


DRILLDOWN = DrilldownConfig(
    pareto_coverage=Decimal("0.8"), max_contributors=5, min_contributor=Decimal(250_000)
)


@given(
    deltas=st.lists(st.decimals(min_value=-(10**8), max_value=10**8, places=2), max_size=40),
    data=st.data(),
)
def test_pareto_respects_the_caps_and_is_order_independent(
    deltas: list[Decimal], data: st.DataObject
) -> None:
    contributors = [Contributor(f"K{i:03d}", d) for i, d in enumerate(deltas)]
    shuffled = data.draw(st.permutations(contributors))

    result = pareto(contributors, DRILLDOWN)

    assert pareto(shuffled, DRILLDOWN) == result
    assert len(result.contributors) <= DRILLDOWN.max_contributors
    assert all(abs(c.delta) >= DRILLDOWN.min_contributor for c in result.contributors)
    assert list(result.contributors) == ranked(contributors)[: len(result.contributors)]


@given(value=st.decimals(min_value=-(10**13), max_value=10**13, places=2))
def test_amount_formatting_is_symmetric_and_below_a_thousand_units(value: Decimal) -> None:
    text = format_amount(value)
    unsigned = format_amount(abs(value))

    assert format_amount(value, absolute=True) == unsigned
    assert text in (unsigned, f"{MINUS}{unsigned}")
    number = unsigned.removeprefix("USD ").rstrip("kmbn").replace(",", "")
    if not unsigned.endswith("bn"):
        assert Decimal(number) < 1000
