from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from variance_copilot.agent.schemas import RESPONSE_MODELS, Answer, Commentary

SCOPE = {
    "company_codes": ["US10"],
    "period_label": "FY2026 P01-P09 YTD",
    "comparison": "BUD26",
    "currency_basis": "GC",
    "basis": "IFRS",
}


def driver(**overrides: Any) -> dict[str, Any]:
    return {
        "title": "CARDIVEX loss of exclusivity from May",
        "impact_fact_id": "F4-12",
        "classification": "LOSS_OF_EXCLUSIVITY",
        "narrative": "Generic entry reduced CARDIVEX net sales by {{F4-12|abs}}.",
        "recurrence": "RECURRING",
        **overrides,
    }


def answer(**overrides: Any) -> dict[str, Any]:
    return {
        "headline": "US10 net sales were {{F2-3|abs}} below budget.",
        "scope": SCOPE,
        "drivers": [driver()],
        **overrides,
    }


def test_appendix_c_payload_validates() -> None:
    payload = answer(
        drivers=[
            driver(evidence_fact_ids=["F4-13", "F4-14"], citation_ids=["MEMO-BUD26#3"]),
            driver(
                title="LUMORA government rebate true-up in P08",
                impact_fact_id="F5-2",
                classification="GTN_PRIOR_PERIOD",
                recurrence="NON_RECURRING",
                citation_ids=["POL-001#5"],
            ),
        ],
        caveats=["US10 reports in USD, so there is no FX effect."],
        citation_ids=["MEMO-BUD26#3", "POL-001#5"],
        follow_up_questions=["How does this compare with the latest forecast (FC226)?"],
    )

    parsed = Answer.model_validate(payload)

    assert [d.impact_fact_id for d in parsed.drivers] == ["F4-12", "F5-2"]
    assert parsed.scope.currency_basis == "GC"


def test_optional_fields_default_to_empty() -> None:
    parsed = Answer.model_validate(answer())

    assert parsed.scope.preliminary is False
    assert (parsed.caveats, parsed.citation_ids, parsed.follow_up_questions) == ([], [], [])
    assert (parsed.drivers[0].evidence_fact_ids, parsed.drivers[0].citation_ids) == ([], [])


@pytest.mark.parametrize(
    ("build", "limit"),
    [
        (lambda text: answer(headline=text), 300),
        (lambda text: answer(drivers=[driver(title=text)]), 120),
        (lambda text: answer(drivers=[driver(narrative=text)]), 800),
    ],
    ids=["headline", "driver title", "driver narrative"],
)
def test_text_length_limits(build: Any, limit: int) -> None:
    Answer.model_validate(build("x" * limit))

    with pytest.raises(ValidationError, match="at most"):
        Answer.model_validate(build("x" * (limit + 1)))


@pytest.mark.parametrize(
    ("build", "limit"),
    [
        (lambda n: answer(drivers=[driver()] * n), 8),
        (lambda n: answer(follow_up_questions=["Why?"] * n), 3),
    ],
    ids=["drivers", "follow_up_questions"],
)
def test_list_length_limits(build: Any, limit: int) -> None:
    Answer.model_validate(build(limit))

    with pytest.raises(ValidationError, match="at most"):
        Answer.model_validate(build(limit + 1))


@pytest.mark.parametrize(
    "payload",
    [
        answer(drivers=[driver(classification="WEATHER")]),
        answer(drivers=[driver(recurrence="SOMETIMES")]),
        answer(scope={**SCOPE, "currency_basis": "EUR"}),
        answer(scope={**SCOPE, "basis": "GAAP"}),
    ],
    ids=["classification", "recurrence", "currency_basis", "basis"],
)
def test_values_outside_the_enumerations_are_rejected(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Answer.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [answer(confidence="high"), answer(drivers=[driver(amount="-318.4m")])],
    ids=["answer", "driver"],
)
def test_unknown_fields_are_rejected(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Answer.model_validate(payload)


def test_commentary_sections() -> None:
    section = {"section": "NET_SALES", "headline": "Net sales below budget.", "drivers": [driver()]}

    parsed = Commentary.model_validate({"scope": SCOPE, "sections": [section]})

    assert parsed.sections[0].section == "NET_SALES"
    assert parsed.anomalies_to_review == []
    with pytest.raises(ValidationError):
        Commentary.model_validate({"scope": SCOPE, "sections": [{**section, "section": "TAX"}]})


def _objects(schema: Any) -> list[dict[str, Any]]:
    if isinstance(schema, dict):
        found = [schema] if schema.get("type") == "object" else []
        return found + [o for value in schema.values() for o in _objects(value)]
    if isinstance(schema, list):
        return [o for value in schema for o in _objects(value)]
    return []


@pytest.mark.parametrize("model", RESPONSE_MODELS, ids=lambda m: m.__name__)
def test_response_schemas_forbid_additional_properties(model: type[BaseModel]) -> None:
    objects = _objects(model.model_json_schema())

    assert objects
    assert all(o.get("additionalProperties") is False for o in objects)
