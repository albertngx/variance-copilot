from __future__ import annotations

from decimal import Decimal

import pytest

from variance_copilot.finance.variance import Magnitude, variance
from variance_copilot.tools.facts import FactKind, FactLedger, FactRecorder, arguments_sha256
from variance_copilot.tools.inputs import PeriodRangeInput, ScopeInput


def _recorder(call: int = 3) -> FactRecorder:
    return FactRecorder(call, "compare", "ab" * 32)


def test_ids_are_call_number_and_sequence() -> None:
    recorder = _recorder()

    first = recorder.add(FactKind.AMOUNT, Decimal(1), "USD", "a", row_key="total", field="actual")
    second = recorder.add(FactKind.AMOUNT, Decimal(2), "USD", "b", row_key="total", field="delta")

    assert (first.fact_id, second.fact_id) == ("F3-1", "F3-2")
    assert [f.fact_id for f in recorder.facts] == ["F3-1", "F3-2"]
    assert recorder.tool_call_id == "tc-3"


def test_provenance_records_the_call_row_and_field() -> None:
    recorder = _recorder()
    recorder.add(
        FactKind.AMOUNT, Decimal(5), "USD", "x", row_key="brand=CARDIVEX|company_code=US10",
        field="delta_total",
    )  # fmt: skip

    provenance = recorder.facts[0].provenance

    assert provenance.tool_call_id == "tc-3"
    assert provenance.tool == "compare"
    assert provenance.arguments_sha256 == "ab" * 32
    assert provenance.row_key == "brand=CARDIVEX|company_code=US10"
    assert provenance.field == "delta_total"


def test_variance_facts_carry_favorable_and_magnitude() -> None:
    recorder = _recorder()
    cost_overrun = variance(Decimal(-110), Decimal(-100))

    recorder.add(
        FactKind.AMOUNT, cost_overrun.delta, "USD", "v", row_key="k", field="delta",
        variance=cost_overrun,
    )  # fmt: skip
    recorder.add(FactKind.AMOUNT, Decimal(1), "USD", "a", row_key="k", field="actual")
    delta, plain = recorder.facts

    assert (delta.favorable, delta.magnitude) == (False, Magnitude.UP)
    assert (plain.favorable, plain.magnitude) == (None, None)


@pytest.mark.parametrize(
    ("value", "stored"),
    [
        ("3773366947.32", "3773366947.32"),
        ("0", "0"),
        ("-9.465813282518937140617325443", "-9.4658132825"),
        ("1.12345678905", "1.1234567890"),
    ],
)
def test_values_keep_their_scale_up_to_ten_decimals(value: str, stored: str) -> None:
    recorder = _recorder()
    recorder.add(FactKind.PERCENT, Decimal(value), "%", "p", row_key="k", field="delta_pct")

    assert str(recorder.facts[0].value) == stored


def test_json_values_are_strings() -> None:
    recorder = _recorder()
    recorder.add(FactKind.AMOUNT, Decimal("-1.50"), "USD", "a", row_key="k", field="f")

    assert recorder.facts[0].model_dump(mode="json")["value"] == "-1.50"


def test_ledger_rejects_duplicate_ids() -> None:
    ledger = FactLedger()
    recorder = _recorder()
    recorder.add(FactKind.COUNT, Decimal(1), "COUNT", "c", row_key="k", field="f")
    ledger.add(recorder.facts)

    with pytest.raises(ValueError, match="F3-1"):
        ledger.add(recorder.facts)
    assert len(ledger) == 1
    assert ledger.get("F3-1") == recorder.facts[0]
    assert ledger.get("F9-9") is None


def test_arguments_digest_is_canonical() -> None:
    one = ScopeInput.model_validate({"company_codes": ["US10"], "brand": "CARDIVEX"})
    two = ScopeInput.model_validate({"brand": "CARDIVEX", "company_codes": ["US10"]})
    other = ScopeInput.model_validate({"company_codes": ["ES10"]})

    assert arguments_sha256(one) == arguments_sha256(two)
    assert arguments_sha256(one) != arguments_sha256(other)
    assert len(arguments_sha256(one)) == 64


def test_arguments_digest_uses_the_public_field_names() -> None:
    period = PeriodRangeInput.model_validate({"from": "2026-05", "to": "2026-09"})

    assert arguments_sha256(period) == arguments_sha256(
        PeriodRangeInput(start="2026-05", end="2026-09")
    )
