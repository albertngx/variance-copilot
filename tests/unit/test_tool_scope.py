from __future__ import annotations

import pytest
from pydantic import ValidationError

from variance_copilot.config import Role
from variance_copilot.finance.codes import ErrorCode, FinanceError, WarningCode
from variance_copilot.finance.materiality import ScopeLevel
from variance_copilot.finance.periods import Period, PeriodView, RangeSelection, ViewSelection
from variance_copilot.tools.context import Principal
from variance_copilot.tools.inputs import PeriodRangeInput, PeriodViewInput, ScopeInput
from variance_copilot.tools.scope import resolve_scope, visible_companies

ALL = ["CH10", "DE10", "ES10", "IE10", "US10"]
ANALYST_ES = Principal("analyst_es", frozenset({Role.ANALYST}), frozenset({"ES10", "DE10"}))
GROUP_FPA = Principal("group_fpa", frozenset({Role.GROUP_ANALYST}), frozenset(ALL))


def test_group_claim_expands_to_every_company_code() -> None:
    principal = Principal.from_claims("group_fpa", [Role.GROUP_ANALYST], ["group"], ALL)

    assert principal.company_scope == frozenset(ALL)
    assert principal.is_group_analyst
    assert principal.has_finance_role


def test_claims_keep_only_known_company_codes() -> None:
    principal = Principal.from_claims("x", [Role.ANALYST], ["ES10", "ZZ99"], ALL)

    assert principal.company_scope == frozenset({"ES10"})
    assert not principal.is_group_analyst


@pytest.mark.parametrize("role", [Role.AUDITOR, Role.ADMIN, Role.EVAL])
def test_non_finance_roles(role: Role) -> None:
    assert not Principal("x", frozenset({role}), frozenset({"ES10"})).has_finance_role


def test_no_requested_codes_means_everything_visible() -> None:
    resolved = resolve_scope(ScopeInput(), ANALYST_ES)

    assert resolved.level is ScopeLevel.COMPANY_CODE
    assert resolved.company_codes == ("DE10", "ES10")
    assert resolved.warnings == ()
    assert not resolved.third_party_only


def test_requested_codes_outside_scope_give_partial_scope() -> None:
    resolved = resolve_scope(ScopeInput(company_codes=("ES10", "US10")), ANALYST_ES)

    assert resolved.company_codes == ("ES10",)
    assert resolved.warnings == (WarningCode.PARTIAL_SCOPE,)


def test_only_codes_outside_scope_is_no_access() -> None:
    with pytest.raises(FinanceError) as excinfo:
        resolve_scope(ScopeInput(company_codes=("US10",)), ANALYST_ES)

    assert excinfo.value.code is ErrorCode.NO_ACCESS
    assert excinfo.value.message == "no access to US10"


def test_group_needs_the_group_analyst_role() -> None:
    with pytest.raises(FinanceError) as excinfo:
        resolve_scope(ScopeInput(level=ScopeLevel.GROUP), ANALYST_ES)

    assert excinfo.value.code is ErrorCode.FORBIDDEN


def test_group_covers_everything_and_excludes_intercompany() -> None:
    resolved = resolve_scope(ScopeInput(level=ScopeLevel.GROUP, brand="CARDIVEX"), GROUP_FPA)

    assert resolved.company_codes == tuple(ALL)
    assert resolved.third_party_only
    assert resolved.filters == {"brand": "CARDIVEX"}
    assert resolved.label() == "Group brand=CARDIVEX"


def test_group_with_company_codes_is_rejected() -> None:
    with pytest.raises(FinanceError) as excinfo:
        resolve_scope(ScopeInput(level=ScopeLevel.GROUP, company_codes=("US10",)), GROUP_FPA)

    assert excinfo.value.code is ErrorCode.INVALID_ARGUMENTS


def test_visible_companies_are_sorted() -> None:
    assert visible_companies(["ES10", "DE10"], ANALYST_ES) == (("DE10", "ES10"), ())


def test_period_inputs_convert_to_selections() -> None:
    view = PeriodViewInput.model_validate({"fiscal_year": 2026, "period": 9, "view": "YTD"})
    span = PeriodRangeInput.model_validate({"from": "2026-05", "to": "2026-09"})

    assert view.selection() == ViewSelection(2026, 9, PeriodView.YTD)
    assert view.label() == "YTD P09 2026"
    assert span.selection() == RangeSelection(Period(2026, 5), Period(2026, 9))
    assert span.label() == "2026-05..2026-09"


@pytest.mark.parametrize(
    "scope",
    [
        {"company_codes": ["US10'; RESET ROLE; --"]},
        {"brand": "CARDIVEX' OR '1'='1"},
        {"cost_center": "x"},
        {"functional_area": "F300 OR 1=1"},
        {"company_scope": ["US10"]},
        {"level": "EVERYTHING"},
    ],
)
def test_scope_rejects_anything_but_codes(scope: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ScopeInput.model_validate(scope)
