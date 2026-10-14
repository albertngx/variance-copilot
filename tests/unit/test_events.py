from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

import pytest

from variance_copilot.generator.events import IMPLEMENTED, active_events
from variance_copilot.generator.model import Master
from variance_copilot.generator.pipeline import build_ledger, prepare
from variance_copilot.generator.seed import Generated, generate
from variance_copilot.generator.truth import difference, pnl_frame

RUN = prepare("ci")


@pytest.fixture(scope="module")
def generated() -> Generated:
    return generate(RUN)


def _headers(generated: Generated) -> dict[str, dict]:
    return {
        f"{h['company_code']}/{h['fiscal_year']}/{h['document_number']}": h
        for h in generated.ledger.journal.headers
    }


def test_ci_runs_its_five_events(generated: Generated) -> None:
    assert generated.ledger.events == ["E01", "E02", "E05", "E06", "E07"]
    assert set(generated.truths) == set(RUN.profile.events)


def test_unimplemented_events_are_skipped() -> None:
    dev = prepare("dev")
    active = active_events(dev.scenario, dev.profile.events)

    assert set(active) == IMPLEMENTED
    assert active_events(dev.scenario, dev.profile.events, exclude={"E01"}) == sorted(
        IMPLEMENTED - {"E01"}
    )


@pytest.mark.parametrize("event_id", RUN.profile.events)
def test_impact_lands_in_target_range(generated: Generated, event_id: str) -> None:
    target = RUN.scenario.event(event_id).target
    value = generated.truths[event_id].target_value

    assert target is not None
    assert value is not None
    assert target.low - Decimal(1) <= value <= target.high + Decimal(1)


def test_misposting_moves_june_cost_to_rd_and_nets_to_zero(generated: Generated) -> None:
    master = Master(generated.ledger.master)
    diff = difference(
        pnl_frame(generated.ledger.journal.lines, master), generated.without_event["E06"]
    )
    by_line: dict[tuple[str, int], Decimal] = defaultdict(Decimal)
    for (company_code, _, period, _, pnl_line, _, _, _), (lc, _) in diff.items():
        if company_code == "ES10":
            by_line[pnl_line, period] += lc

    amount = RUN.scenario.event("E06").amount_lc
    assert by_line["RESEARCH_DEVELOPMENT", 6] == -amount
    assert by_line["SELLING_MARKETING", 6] == amount
    for pnl_line in ("RESEARCH_DEVELOPMENT", "SELLING_MARKETING"):
        assert by_line[pnl_line, 6] + by_line[pnl_line, 7] == 0


def test_true_up_lines_carry_the_assignment(generated: Generated) -> None:
    event = RUN.scenario.event("E07")
    documents = set(generated.truths["E07"].documents)
    lines = [
        line
        for line in generated.ledger.journal.lines
        if f"{line['company_code']}/{line['fiscal_year']}/{line['document_number']}" in documents
    ]

    assert lines
    assert {line["assignment"] for line in lines} == {event.assignment}
    assert {line["gl_account"] for line in lines} == {event.gl_account, event.offset_account}


def test_event_documents_exist_with_their_header(generated: Generated) -> None:
    headers = _headers(generated)
    for event_id in ("E05", "E06", "E07"):
        event = RUN.scenario.event(event_id)
        documents = generated.truths[event_id].documents
        assert documents
        assert all(d in headers for d in documents)
        texts = {headers[d]["header_text"] for d in documents}
        assert event.document.header_text in texts
    for truth in generated.truths.values():
        assert all(d in headers for d in truth.documents)


def test_events_never_touch_the_budget(generated: Generated) -> None:
    without = build_ledger(RUN, exclude=set(RUN.profile.events))

    for budget, reference in zip(generated.ledger.budgets, without.budgets, strict=True):
        assert budget.sales == reference.sales
        assert budget.spend == reference.spend


def test_fx_event_only_moves_chf(generated: Generated) -> None:
    without = build_ledger(RUN, exclude={"E02"})
    changed = {
        (r.rate_type, r.from_currency)
        for r, s in zip(generated.ledger.fx_rates, without.fx_rates, strict=True)
        if r != s
    }

    assert changed
    assert {currency for _, currency in changed} == {"CHF"}


def test_loss_of_exclusivity_erodes_volume_from_may(generated: Generated) -> None:
    truth = generated.truths["E01"]

    assert set(truth.periods) == {5, 6, 7, 8, 9}
    assert all(value < 0 for value in truth.by_period.values())
    assert truth.by_line["COGS"] > 0  # lower cost of goods partly offsets
