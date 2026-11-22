from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from variance_copilot.agent.renderer import CitedChunk, RenderedAnswer, RenderError, render_answer
from variance_copilot.agent.schemas import Answer
from variance_copilot.tools.facts import Fact, FactKind, FactLedger, Provenance

M = "\N{MINUS SIGN}"
EN = "\N{EN DASH}"
DOT = "\N{MIDDLE DOT}"
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
RUN_ID = UUID("3f6c2a90-5b1e-4c8d-9a47-0e2f6b8d1c35")
SCOPE: dict[str, Any] = {
    "company_codes": ["US10"],
    "period_label": "FY2026 P09 YTD",
    "comparison": "BUD26",
    "currency_basis": "GC",
    "basis": "IFRS",
}


def fact(fact_id: str, value: str, kind: FactKind = FactKind.AMOUNT, unit: str = "USD") -> Fact:
    return Fact(
        fact_id=fact_id,
        kind=kind,
        value=Decimal(value),
        unit=unit,
        label=fact_id,
        provenance=Provenance(
            tool_call_id="tc-1",
            tool="compare",
            arguments_sha256="0" * 64,
            row_key="",
            field="delta",
        ),
    )


def answer(**fields: Any) -> Answer:
    return Answer.model_validate(
        {"headline": "Net sales were on budget.", "scope": SCOPE, "drivers": [], **fields}
    )


def driver(**fields: Any) -> dict[str, Any]:
    return {
        "title": "Brand A",
        "impact_fact_id": "F1-1",
        "classification": "VOLUME",
        "narrative": "Lower volumes.",
        "recurrence": "RECURRING",
        **fields,
    }


def render(
    to_render: Answer, *facts: Fact, citations: dict[str, CitedChunk] | None = None
) -> RenderedAnswer:
    ledger = FactLedger()
    ledger.add(facts)
    return render_answer(to_render, ledger, run_id=RUN_ID, citations=citations)


# --- Appendix C --------------------------------------------------------------

APPENDIX_C_ANSWER = {
    "headline": "US10 net sales were {{F2-3|abs}} ({{F2-4|abspct}}) below budget YTD P09 2026, "
    "mainly because CARDIVEX lost exclusivity earlier than budgeted.",
    "scope": {**SCOPE, "period_label": "FY2026 P01-P09 YTD", "preliminary": False},
    "drivers": [
        driver(
            title="CARDIVEX loss of exclusivity from May",
            impact_fact_id="F4-12",
            classification="LOSS_OF_EXCLUSIVITY",
            narrative="Generic entry on 1 May 2026, earlier than the budget assumption of "
            "1 September, reduced CARDIVEX net sales by {{F4-12|abs}}: lower volumes "
            "{{F4-13|abs}} and contract price concessions {{F4-14|abs}}.",
            evidence_fact_ids=["F4-13", "F4-14"],
            citation_ids=["MEMO-BUD26#3"],
        ),
        driver(
            title="LUMORA government rebate true-up in P08",
            impact_fact_id="F5-2",
            classification="GTN_PRIOR_PERIOD",
            narrative="A true-up of the government rebate accrual for P01-P07 sales reduced net "
            "sales by {{F5-2|abs}} in P08. Under the gross-to-net policy, this change in "
            "estimate is recognised in the period it is identified.",
            recurrence="NON_RECURRING",
            citation_ids=["POL-001#5"],
        ),
        driver(
            title="Other brands",
            impact_fact_id="F4-30",
            classification="OTHER",
            narrative="All other brands together were {{F4-30|abs}} below budget, with no "
            "single brand above the materiality threshold.",
            recurrence="UNKNOWN",
        ),
    ],
    "caveats": ["US10 reports in USD, so there is no FX effect."],
    "citation_ids": ["MEMO-BUD26#3", "POL-001#5"],
    "follow_up_questions": ["How does this compare with the latest forecast (FC226)?"],
}
APPENDIX_C_FACTS = (
    fact("F2-3", "-365710362.65"),
    fact("F2-4", "-10.51", FactKind.PERCENT, "%"),
    fact("F4-12", "-318412377.21"),
    fact("F4-13", "-282904110.38"),
    fact("F4-14", "-35508266.83"),
    fact("F5-2", "-40000000.00"),
    fact("F4-30", "-7297985.44"),
)
APPENDIX_C_CITATIONS = {
    "MEMO-BUD26#3": CitedChunk("MEMO-BUD26", "3 Key assumptions"),
    "POL-001#5": CitedChunk("POL-001", "5 True-ups"),
}
APPENDIX_C_MARKDOWN = (
    "**US10 net sales were USD 365.7m (10.5%) below budget YTD P09 2026, mainly because "
    "CARDIVEX lost exclusivity earlier than budgeted.**\n"
    "\n"
    f"*Scope: US10 {DOT} FY2026 P01{EN}P09 YTD {DOT} vs BUD26 {DOT} USD {DOT} IFRS*\n"
    "\n"
    f"1. **CARDIVEX loss of exclusivity from May ({M}USD 318.4m).** Generic entry on 1 May "
    "2026, earlier than the budget assumption of 1 September, reduced CARDIVEX net sales by "
    "USD 318.4m: lower volumes USD 282.9m and contract price concessions USD 35.5m. "
    "*(MEMO-BUD26 §3 Key assumptions)*\n"
    f"2. **LUMORA government rebate true-up in P08 ({M}USD 40.0m).** A true-up of the "
    f"government rebate accrual for P01{EN}P07 sales reduced net sales by USD 40.0m in P08. "
    "Under the gross-to-net policy, this change in estimate is recognised in the period it is "
    "identified. *(POL-001 §5 True-ups)*\n"
    f"3. **Other brands ({M}USD 7.3m).** All other brands together were USD 7.3m below "
    "budget, with no single brand above the materiality threshold.\n"
    "\n"
    "*Caveat: US10 reports in USD, so there is no FX effect.*\n"
    "\n"
    f"*AI-generated answer {DOT} run 3f6c{ELLIPSIS} {DOT} all figures traceable via* "
    f"`GET /v1/runs/{RUN_ID}/facts`"
)


def test_appendix_c_renders_as_specified() -> None:
    rendered = render(
        Answer.model_validate(APPENDIX_C_ANSWER), *APPENDIX_C_FACTS, citations=APPENDIX_C_CITATIONS
    )

    assert rendered.markdown == APPENDIX_C_MARKDOWN


def test_every_rendered_number_is_in_the_placeholder_mapping() -> None:
    rendered = render(
        Answer.model_validate(APPENDIX_C_ANSWER), *APPENDIX_C_FACTS, citations=APPENDIX_C_CITATIONS
    )

    assert rendered.placeholders == {
        "{{F2-3|abs}}": "USD 365.7m",
        "{{F2-4|abspct}}": "10.5%",
        "{{F4-12}}": f"{M}USD 318.4m",
        "{{F4-12|abs}}": "USD 318.4m",
        "{{F4-13|abs}}": "USD 282.9m",
        "{{F4-14|abs}}": "USD 35.5m",
        "{{F5-2}}": f"{M}USD 40.0m",
        "{{F5-2|abs}}": "USD 40.0m",
        "{{F4-30}}": f"{M}USD 7.3m",
        "{{F4-30|abs}}": "USD 7.3m",
    }


# --- Placeholders ------------------------------------------------------------

SPEC_13_1 = {
    "": {FactKind.AMOUNT, FactKind.QUANTITY, FactKind.COUNT, FactKind.RATE},
    "abs": {FactKind.AMOUNT, FactKind.QUANTITY},
    "pct": {FactKind.PERCENT},
    "abspct": {FactKind.PERCENT},
    "pp": {FactKind.PP},
}


@pytest.mark.parametrize(
    ("modifier", "kind"), [(modifier, kind) for modifier in SPEC_13_1 for kind in FactKind]
)
def test_modifiers_are_allowed_only_for_the_kinds_in_spec_13_1(
    modifier: str, kind: FactKind
) -> None:
    token = f"{{{{F1-1|{modifier}}}}}" if modifier else "{{F1-1}}"
    to_render = answer(headline=f"Moved by {token}.")

    if kind in SPEC_13_1[modifier]:
        assert token in render(to_render, fact("F1-1", "-2.5", kind)).placeholders
    else:
        with pytest.raises(RenderError, match="not allowed"):
            render(to_render, fact("F1-1", "-2.5", kind))


@pytest.mark.parametrize(
    ("kind", "unit", "value", "token", "expected"),
    [
        (FactKind.AMOUNT, "EUR", "-2400000", "{{F1-1}}", f"{M}EUR 2.4m"),
        (FactKind.AMOUNT, "EUR", "-2400000", "{{F1-1|abs}}", "EUR 2.4m"),
        (FactKind.QUANTITY, "FTE", "-12.4", "{{F1-1}}", f"{M}12 FTE"),
        (FactKind.QUANTITY, "FTE", "-12.4", "{{F1-1|abs}}", "12 FTE"),
        (FactKind.COUNT, "", "1204", "{{F1-1}}", "1,204"),
        (FactKind.RATE, "USD/CHF", "1.12005", "{{F1-1}}", "1.1201"),
        (FactKind.PERCENT, "%", "-10.506", "{{F1-1|pct}}", f"{M}10.5%"),
        (FactKind.PERCENT, "%", "-10.506", "{{F1-1|abspct}}", "10.5%"),
        (FactKind.PP, "pp", "2.44", "{{F1-1|pp}}", "2.4 pp"),
    ],
)
def test_each_kind_renders_with_the_spec_9_10_format(
    kind: FactKind, unit: str, value: str, token: str, expected: str
) -> None:
    rendered = render(answer(headline=f"Moved by {token}."), fact("F1-1", value, kind, unit))

    assert rendered.placeholders == {token: expected}
    assert rendered.markdown.startswith(f"**Moved by {expected}.**")


@pytest.mark.parametrize(
    "headline",
    [
        "Down {{F3}}.",
        "Down {{F3-2|foo}}.",
        "Down {{ F3-2 }}.",
        "Down {{f3-2}}.",
        "Down {{F3-2|abs|pct}}.",
        "Down {{F3-2 and more.",
        "Down F3-2}} and more.",
    ],
)
def test_malformed_placeholders_are_errors(headline: str) -> None:
    with pytest.raises(RenderError, match=r"placeholder|modifier"):
        render(answer(headline=headline), fact("F3-2", "-1"))


def test_a_placeholder_for_a_fact_outside_the_run_is_an_error() -> None:
    with pytest.raises(RenderError, match="no fact F9-9"):
        render(answer(headline="Down {{F9-9|abs}}."), fact("F1-1", "-1"))


def test_a_driver_impact_outside_the_run_is_an_error() -> None:
    with pytest.raises(RenderError, match="no fact F9-9"):
        render(answer(drivers=[driver(impact_fact_id="F9-9")]), fact("F1-1", "-1"))


def test_a_percent_impact_renders_as_a_percentage() -> None:
    rendered = render(answer(drivers=[driver()]), fact("F1-1", "-8.06", FactKind.PERCENT, "%"))

    assert f"1. **Brand A ({M}8.1%).** Lower volumes." in rendered.markdown
    assert rendered.placeholders == {"{{F1-1|pct}}": f"{M}8.1%"}


# --- Citations ---------------------------------------------------------------

POLICIES = {
    "POL-005#7": CitedChunk("POL-005", "4 Milestones > 4.2 Capitalisation"),
    "POL-001#5": CitedChunk("POL-001", "5 True-ups"),
}


def test_a_citation_shows_the_document_and_the_last_section_of_its_path() -> None:
    to_render = answer(drivers=[driver(citation_ids=["POL-005#7", "POL-001#5"])])

    rendered = render(to_render, fact("F1-1", "-1"), citations=POLICIES)

    assert "*(POL-005 §4.2 Capitalisation; POL-001 §5 True-ups)*" in rendered.markdown


def test_answer_citations_no_driver_shows_are_listed_as_sources() -> None:
    to_render = answer(
        drivers=[driver(citation_ids=["POL-001#5"])], citation_ids=["POL-001#5", "POL-005#7"]
    )

    rendered = render(to_render, fact("F1-1", "-1"), citations=POLICIES)

    assert "\n\n*Sources: POL-005 §4.2 Capitalisation*\n\n" in rendered.markdown
    assert rendered.markdown.count("POL-001 §5") == 1


def test_a_citation_not_retrieved_in_the_run_is_an_error() -> None:
    to_render = answer(drivers=[driver(citation_ids=["POL-009#1"])])

    with pytest.raises(RenderError, match="POL-009#1"):
        render(to_render, fact("F1-1", "-1"), citations=POLICIES)


# --- Layout ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scope", "line"),
    [
        (
            {
                "company_codes": ["ES10", "DE10"],
                "period_label": "FY2026 P06",
                "comparison": "FC226",
                "currency_basis": "LC",
                "basis": "CORE",
                "preliminary": True,
            },
            f"*Scope: ES10, DE10 {DOT} FY2026 P06 {DOT} vs FC226 {DOT} local currency {DOT} "
            f"core {DOT} preliminary*",
        ),
        (
            {**SCOPE, "currency_basis": "CC", "comparison": "PY"},
            f"*Scope: US10 {DOT} FY2026 P09 YTD {DOT} vs PY {DOT} constant currency {DOT} IFRS*",
        ),
    ],
)
def test_scope_line(scope: dict[str, Any], line: str) -> None:
    assert render(answer(scope=scope)).markdown.split("\n\n")[1] == line


def test_period_ranges_get_an_en_dash_and_other_hyphens_stay() -> None:
    to_render = answer(headline="Gross-to-net true-ups in P05-P09, posted 2026-08-31.")

    assert render(to_render).markdown.startswith(
        f"**Gross-to-net true-ups in P05{EN}P09, posted 2026-08-31.**"
    )


def test_an_answer_without_drivers_has_headline_scope_caveats_and_footer() -> None:
    blocks = render(answer(caveats=["First.", "Second."])).markdown.split("\n\n")

    assert blocks[0] == "**Net sales were on budget.**"
    assert blocks[2:4] == ["*Caveat: First.*", "*Caveat: Second.*"]
    assert blocks[4].startswith("*AI-generated answer")
    assert len(blocks) == 5
