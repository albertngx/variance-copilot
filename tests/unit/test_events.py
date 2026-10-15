from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

import pytest

from variance_copilot.config import load_materiality
from variance_copilot.generator.events import active_events
from variance_copilot.generator.model import Master
from variance_copilot.generator.pipeline import build_ledger, prepare
from variance_copilot.generator.seed import Generated, generate
from variance_copilot.generator.truth import difference

CI, DEV = prepare("ci"), prepare("dev")
ALL_EVENTS = [e.id for e in DEV.scenario.events]


@pytest.fixture(scope="module")
def ci() -> Generated:
    return generate(CI)


@pytest.fixture(scope="module")
def dev() -> Generated:
    return generate(DEV)


def _documents(generated: Generated, event_id: str) -> list[dict]:
    documents = set(generated.truths[event_id].documents)
    return [
        line
        for line in generated.ledger.journal.lines
        if f"{line['company_code']}/{line['fiscal_year']}/{line['document_number']}" in documents
    ]


def _diff_by(generated: Generated, event_id: str, *fields: int) -> dict[tuple, Decimal]:
    diff = difference(generated.full.actual, generated.without_event[event_id].actual)
    totals: dict[tuple, Decimal] = defaultdict(Decimal)
    for key, (lc, _) in diff.items():
        totals[tuple(key[f] for f in fields)] += lc
    return totals


def test_profiles_run_their_events(ci: Generated, dev: Generated) -> None:
    assert ci.ledger.events == ["E01", "E02", "E05", "E06", "E07"]
    assert dev.ledger.events == ALL_EVENTS
    assert active_events(DEV.scenario, DEV.profile.events, exclude={"E01"}) == ALL_EVENTS[1:]


@pytest.mark.parametrize("profile", ["ci", "dev"])
def test_every_target_is_met(profile: str, request: pytest.FixtureRequest) -> None:
    generated: Generated = request.getfixturevalue(profile)
    for event_id, truth in generated.truths.items():
        target = DEV.scenario.event(event_id).target
        if target is None:
            continue
        assert truth.target_value is not None, event_id
        assert target.low - 1 <= truth.target_value <= target.high + 1, event_id


def test_misposting_moves_june_cost_to_rd_and_nets_to_zero(ci: Generated) -> None:
    by_line = _diff_by(ci, "E06", 0, 4, 2)
    amount = CI.scenario.event("E06").amount_lc

    assert by_line["ES10", "RESEARCH_DEVELOPMENT", 6] == -amount
    assert by_line["ES10", "SELLING_MARKETING", 6] == amount
    for line in ("RESEARCH_DEVELOPMENT", "SELLING_MARKETING"):
        assert by_line["ES10", line, 6] + by_line["ES10", line, 7] == 0


def test_cut_off_error_shifts_cost_from_march_to_april(dev: Generated) -> None:
    by_period = _diff_by(dev, "E09", 0, 4, 2)
    amount = DEV.scenario.event("E09").amount_lc

    assert by_period["DE10", "SELLING_MARKETING", 3] == amount
    assert by_period["DE10", "SELLING_MARKETING", 4] == -amount
    lines = _documents(dev, "E09")
    assert {line["period"] for line in lines} == {4}


def test_true_up_lines_carry_the_assignment(ci: Generated) -> None:
    event = CI.scenario.event("E07")
    lines = _documents(ci, "E07")

    assert lines
    assert {line["assignment"] for line in lines} == {event.assignment}
    assert {line["gl_account"] for line in lines} == {event.gl_account, event.offset_account}


def test_milestone_is_capitalised_in_usd_without_pnl_impact(dev: Generated) -> None:
    lines = _documents(dev, "E04b")
    event = DEV.scenario.event("E04b")

    assert {line["gl_account"] for line in lines} == {event.debit_account, event.credit_account}
    assert {line["currency_tc"] for line in lines} == {"USD"}
    assert (
        sum(line["amount_tc"] for line in lines if line["debit_credit"] == "S") == event.amount_tc
    )
    assert dev.truths["E04b"].by_line == {}
    assert all(v == 0 for v in dev.truths["E04b"].by_version["BUD26"].values())


def test_rd_acceleration_overruns_trials_in_april_to_july(dev: Generated) -> None:
    by_period = _diff_by(dev, "E04a", 0, 4, 2)
    event = DEV.scenario.event("E04a")
    share = event.amount_lc / len(event.periods)

    for period in event.periods:
        assert by_period["CH10", "RESEARCH_DEVELOPMENT", period] == pytest.approx(-share, abs=1)


def test_transfer_price_change_nets_to_zero_for_the_group(dev: Generated) -> None:
    diff = difference(dev.full.actual, dev.without_event["E10"].actual)
    by_company: dict[str, Decimal] = defaultdict(Decimal)
    group = Decimal(0)
    for key, (_, gc) in diff.items():
        by_company[key[0]] += gc
        if not key[7]:
            group += gc

    assert by_company["ES10"] > 0
    assert by_company["CH10"] == pytest.approx(-by_company["ES10"], abs=Decimal(10))
    assert abs(group) <= 1


def test_restructuring_provisions(dev: Generated) -> None:
    lines = [line for line in _documents(dev, "E08") if line["gl_account"] == "70000000"]
    amounts = {line["company_code"]: line["amount_lc"] for line in lines}
    expected = {p.company_code: p.amount_lc for p in DEV.scenario.event("E08").provisions}

    assert amounts == expected
    assert {line["functional_area"] for line in lines} == {"F600"}


def test_divestment_gain_is_in_fy2025(dev: Generated) -> None:
    truth = dev.truths["E11"]

    assert truth.fiscal_year == 2025
    assert set(truth.by_version) == {"BUD25"}
    assert truth.by_line == {"OTHER_INCOME": DEV.scenario.event("E11").amount_tc}


def test_decoys_are_planted_and_immaterial(dev: Generated) -> None:
    event = DEV.scenario.event("E12")
    decoys = dev.truths["E12"].details
    floor = load_materiality().pnl_line.company_code.relative_floor

    assert len(decoys) == event.count
    assert len({(d["cost_center"], d["gl_account"]) for d in decoys}) == event.count
    for decoy in decoys:
        assert event.min_abs_usd <= decoy["amount_usd"] <= event.max_abs_usd
        assert decoy["amount_usd"] < floor


def test_stable_brand_within_its_band(dev: Generated) -> None:
    event = DEV.scenario.event("E12")
    actual: dict[str, Decimal] = defaultdict(Decimal)
    plan: dict[str, Decimal] = defaultdict(Decimal)
    for frame, totals in ((dev.full.actual, actual), (dev.full.plans["BUD26"], plan)):
        for key, (_, gc) in frame.items():
            in_band = key[1] == 2026 and key[2] <= 9 and key[6] == event.stable_brand
            if in_band and not key[7] and key[5] in ("GROSS_SALES", "GTN_DEDUCTION"):
                totals[key[0]] += gc
    for company_code, planned in plan.items():
        assert abs(actual[company_code] / planned - 1) <= event.stable_tolerance


def test_event_documents_exist_with_their_header(dev: Generated) -> None:
    headers = {
        f"{h['company_code']}/{h['fiscal_year']}/{h['document_number']}": h
        for h in dev.ledger.journal.headers
    }
    for truth in dev.truths.values():
        assert all(d in headers for d in truth.documents), truth.event_id
    for event_id in ("E04b", "E05", "E06", "E07", "E09"):
        texts = {headers[d]["header_text"] for d in dev.truths[event_id].documents}
        assert DEV.scenario.event(event_id).document.header_text in texts


def test_events_never_touch_the_budget(ci: Generated) -> None:
    without = build_ledger(CI, exclude=set(CI.profile.events))

    for budget, reference in zip(ci.ledger.budgets, without.budgets, strict=True):
        assert budget.sales == reference.sales
        assert budget.spend == reference.spend


def test_fx_event_only_moves_chf(ci: Generated) -> None:
    without = build_ledger(CI, exclude={"E02"})
    changed = {
        r.from_currency for r, s in zip(ci.ledger.fx_rates, without.fx_rates, strict=True) if r != s
    }

    assert changed == {"CHF"}


def test_loss_of_exclusivity_erodes_volume_from_may(ci: Generated) -> None:
    truth = ci.truths["E01"]

    assert set(truth.periods) == {5, 6, 7, 8, 9}
    assert all(value < 0 for value in truth.by_version["BUD26"].values())
    assert truth.by_line["COGS"] > 0


def test_master_lookup_is_shared(ci: Generated) -> None:
    assert Master(ci.ledger.master).channel_of("C100001") == "WHOLESALER"
