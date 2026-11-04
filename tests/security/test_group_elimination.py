from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import replace
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection

from variance_copilot.config import Role
from variance_copilot.generator.pipeline import Run, prepare
from variance_copilot.tools.context import Principal
from variance_copilot.tools.results import ToolErrorResult
from variance_copilot.tools.session import ToolSession

GROUP_FPA = Principal(
    "group_fpa", frozenset({Role.GROUP_ANALYST}), frozenset({"CH10", "ES10", "IE10", "US10"})
)
YTD_P09 = {"fiscal_year": 2026, "period": 9, "view": "YTD"}


def _ci_with(events: list[str]) -> Run:
    run = prepare("ci", 20261010)
    return replace(run, profile=run.profile.model_copy(update={"events": events}))


def _actuals(session: ToolSession, scope: dict[str, Any]) -> dict[str, Decimal]:
    result = session.call("compare", {"scope": scope, "period": YTD_P09, "comparison": "BUD26"})
    assert not isinstance(result, ToolErrorResult), result
    values = {f.fact_id: f.value for f in result.facts}
    return {str(row.key): values[row.actual.fact_id] for row in result.rows}


def test_e10_moves_es10_but_not_the_group(
    seed_ledger: Callable[[Run], AbstractContextManager[Connection]],
    tool_session: Callable[[Connection, Principal], ToolSession],
) -> None:
    """E10 changes only intercompany prices, so group figures are identical without it."""
    outcomes = []
    for events in (["E10"], []):
        with seed_ledger(_ci_with(events)) as conn:
            session = tool_session(conn, GROUP_FPA)
            group = _actuals(session, {"level": "GROUP"})
            es10 = _actuals(session, {"company_codes": ["ES10"]})
            outcomes.append((group, es10))
    (group_with, es10_with), (group_without, es10_without) = outcomes

    assert group_with == group_without
    assert es10_with != es10_without
