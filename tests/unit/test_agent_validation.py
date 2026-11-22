from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from variance_copilot.agent.providers.base import Usage
from variance_copilot.agent.runs import cost_usd, model_price
from variance_copilot.agent.validation import check_answer
from variance_copilot.config import ConfigError, ModelPrice, ModelPrices
from variance_copilot.tools.facts import Fact, FactKind, FactLedger, Provenance


def fact(fact_id: str, kind: FactKind = FactKind.AMOUNT, value: str = "-41200000") -> Fact:
    return Fact(
        fact_id=fact_id,
        kind=kind,
        value=Decimal(value),
        unit="USD" if kind is FactKind.AMOUNT else "%",
        label=fact_id,
        provenance=Provenance(
            tool_call_id="tc-1", tool="compare", arguments_sha256="0" * 64, row_key="", field=""
        ),
    )


LEDGER = FactLedger()
LEDGER.add([fact("F1-1"), fact("F1-2", FactKind.PERCENT, "-7.5")])


def answer(**overrides: Any) -> str:
    driver = {
        "title": "Lower volume",
        "impact_fact_id": "F1-1",
        "classification": "VOLUME",
        "narrative": "Volumes fell {{F1-2|abspct}} after the loss of exclusivity.",
        "recurrence": "RECURRING",
    }
    body: dict[str, Any] = {
        "headline": "US10 net sales were {{F1-1|abs}} below budget.",
        "scope": {
            "company_codes": ["US10"],
            "period_label": "FY2026 P01-P09 YTD",
            "comparison": "BUD26",
            "currency_basis": "GC",
            "basis": "IFRS",
        },
        "drivers": [driver | overrides.pop("driver", {})],
    }
    return json.dumps(body | overrides)


def violations(text: str) -> list[str]:
    return list(check_answer(text, LEDGER, run_id=uuid4()).violations)


def test_a_valid_answer_renders_with_every_placeholder_mapped() -> None:
    checked = check_answer(answer(), LEDGER, run_id=uuid4())

    assert checked.violations == ()
    assert checked.rendered is not None
    assert set(checked.rendered.placeholders) >= {"{{F1-1|abs}}", "{{F1-2|abspct}}"}


def test_text_that_is_not_json_is_one_violation() -> None:
    (violation,) = violations("The variance was large.")

    assert violation.startswith("answer: Invalid JSON")


def test_schema_errors_name_the_field() -> None:
    body = json.loads(answer())
    del body["headline"]

    assert violations(json.dumps(body)) == ["headline: Field required"]


def test_every_unknown_fact_is_reported_at_once() -> None:
    found = violations(
        answer(headline="Sales fell {{F7-1|abs}}.", driver={"impact_fact_id": "F8-2"})
    )

    assert found == [
        "headline: no fact F7-1 in this run",
        "drivers.0: no fact F8-2 in this run",
    ]


@pytest.mark.parametrize(
    ("headline", "expected"),
    [
        ("Sales fell {{F1-1|pct}}.", "headline: {{F1-1|pct}} is not allowed for a AMOUNT fact"),
        ("Sales fell {{ F1-1 }}.", "headline: malformed placeholder {{ F1-1 }}"),
        ("Sales fell {{F1-1|half}}.", "headline: unknown modifier in {{F1-1|half}}"),
    ],
)
def test_bad_placeholders_are_violations(headline: str, expected: str) -> None:
    assert violations(answer(headline=headline)) == [expected]


def test_citations_must_have_been_retrieved() -> None:
    found = violations(answer(citation_ids=["POL-001#3"]))

    assert found == ["citation POL-001#3 was not retrieved in this run"]


def test_an_unclosed_placeholder_fails_at_rendering() -> None:
    assert violations(answer(headline="Sales fell {{F1-1|abs.")) == [
        "unclosed placeholder in 'Sales fell {{F1-1|abs.'"
    ]


# --- Cost ------------------------------------------------------------------


def test_cost_is_tokens_times_the_rate_per_million() -> None:
    price = ModelPrice(input_per_mtok=Decimal("1.00"), output_per_mtok=Decimal("5.00"))

    assert cost_usd(Usage(50_000, 2_000), price) == Decimal("0.06")


def test_a_model_without_a_price_is_a_config_error() -> None:
    prices = ModelPrices(version=1, currency="USD", models={})

    with pytest.raises(ConfigError, match="no price for model 'm1'"):
        model_price(prices, "m1")
