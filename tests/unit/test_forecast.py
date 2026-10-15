from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

import pytest

from variance_copilot.generator.forecast import CUT_OFFS, PHASED_ACCOUNTS, TRAILING_MONTHS
from variance_copilot.generator.pipeline import all_plan_rows, prepare
from variance_copilot.generator.seed import Generated, generate
from variance_copilot.generator.truth import difference

DEV = prepare("dev")


@pytest.fixture(scope="module")
def dev() -> Generated:
    return generate(DEV)


def _forecast_change(dev: Generated, event_id: str, version: str) -> dict[int, Decimal]:
    """How the event changed the forecast, by period, in the event's company scope."""
    diff = difference(dev.full.plans[version], dev.without_event[event_id].plans[version])
    companies = set(dev.truths[event_id].company_codes) or {"CH10", "DE10", "ES10", "US10"}
    totals: dict[int, Decimal] = defaultdict(Decimal)
    for key, (_, gc) in diff.items():
        if key[1] == 2026 and key[0] in companies:
            totals[key[2]] += gc
    return {p: v for p, v in totals.items() if abs(v) > 1}


@pytest.mark.parametrize("version", list(CUT_OFFS))
def test_actual_months_are_copied_as_booked(dev: Generated, version: str) -> None:
    cut = CUT_OFFS[version]
    actual: dict[tuple, Decimal] = defaultdict(Decimal)
    for key, (_, gc) in dev.full.actual.items():
        if key[1] == 2026 and key[2] <= cut:
            actual[key[0], key[2], key[3]] += gc
    planned: dict[tuple, Decimal] = defaultdict(Decimal)
    for key, (_, gc) in dev.full.plans[version].items():
        if key[2] <= cut:
            planned[key[0], key[2], key[3]] += gc

    assert {k for k, v in actual.items() if v} == {k for k, v in planned.items() if v}
    assert all(planned[k] == v for k, v in actual.items())


@pytest.mark.parametrize("version", list(CUT_OFFS))
def test_remaining_months_are_replanned(dev: Generated, version: str) -> None:
    periods = {
        row["period"] for row in all_plan_rows(dev.ledger) if row["plan_category"] == version
    }
    forecast = next(d for d in dev.ledger.forecasts if d.version == version)

    assert periods == set(range(1, 13))
    assert forecast.periods == list(range(CUT_OFFS[version] + 1, 13))


def test_opex_blends_budget_phasing_with_trailing_actuals(dev: Generated) -> None:
    budget = next(b for b in dev.ledger.budgets if b.fiscal_year == 2026)
    actual = next(a for a in dev.ledger.actuals if a.fiscal_year == 2026)
    forecast = next(d for d in dev.ledger.forecasts if d.version == "FC226")
    cut = CUT_OFFS["FC226"]
    checked = 0
    for key, value in forecast.spend.items():
        cost_center, gl_account, _, wbs = key
        if gl_account in PHASED_ACCOUNTS or cost_center.startswith(
            ("CH10-RD-ONC", "ES10-MKT", "DE10-MKT")
        ):
            continue
        trailing = [
            actual.spend.get((cost_center, gl_account, p, wbs))
            for p in range(cut - TRAILING_MONTHS + 1, cut + 1)
        ]
        if None in trailing or key not in budget.spend:
            continue
        expected = 0.5 * budget.spend[key] + 0.5 * sum(trailing) / TRAILING_MONTHS  # type: ignore[arg-type]
        assert value == pytest.approx(expected)
        checked += 1
    assert checked > 100


@pytest.mark.parametrize(
    ("event_id", "known"),
    [
        ("E01", {"FC226"}),
        ("E02", {"FC126", "FC226"}),
        ("E03", {"FC126", "FC226"}),
        ("E04a", {"FC226"}),
        ("E05", set()),
        ("E07", set()),
        ("E10", {"FC126", "FC226"}),
    ],
)
def test_forecast_months_know_only_visible_events(
    dev: Generated, event_id: str, known: set[str]
) -> None:
    assert set(DEV.scenario.event(event_id).known_by) == known | (
        {"FC226"} if event_id == "E08" else set()
    )
    for version, cut in CUT_OFFS.items():
        replanned = {p: v for p, v in _forecast_change(dev, event_id, version).items() if p > cut}
        assert bool(replanned) == (version in known), (event_id, version)


def test_restructuring_reaches_fc226_through_its_actual_months(dev: Generated) -> None:
    change = _forecast_change(dev, "E08", "FC226")

    assert set(change) == {6}
    assert _forecast_change(dev, "E08", "FC126") == {}


def test_launch_signal_grows_from_fc126_to_fc226(dev: Generated) -> None:
    early = _forecast_change(dev, "E03", "FC126")
    late = _forecast_change(dev, "E03", "FC226")

    assert 0 < early[9] < late[9]


def test_pull_forward_lowers_the_last_quarter_in_fc226(dev: Generated) -> None:
    change = _forecast_change(dev, "E04a", "FC226")

    assert change[7] < 0
    assert all(change[p] > 0 for p in (10, 11, 12))


def test_variance_against_fc226_is_small_for_known_and_full_for_unknown(dev: Generated) -> None:
    def ytd(event_id: str, version: str) -> Decimal:
        return dev.truths[event_id].ytd(version)

    assert abs(ytd("E01", "FC226")) < Decimal("0.05") * abs(ytd("E01", "BUD26"))
    assert abs(ytd("E02", "FC226")) < Decimal("0.25") * abs(ytd("E02", "BUD26"))
    for event_id in ("E05", "E07"):
        assert ytd(event_id, "FC226") == ytd(event_id, "BUD26")


def test_forecasts_carry_fte_for_every_month(dev: Generated) -> None:
    for forecast in dev.ledger.forecasts:
        assert {period for _, period in forecast.fte} == set(range(1, 13))
