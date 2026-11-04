"""Result shapes shared by every tool (SPEC §10.1 rules 3, 4 and 7)."""

from __future__ import annotations

from pydantic import BaseModel

from variance_copilot.finance.codes import ErrorCode, WarningCode
from variance_copilot.finance.materiality import ScopeLevel
from variance_copilot.tools.facts import Fact
from variance_copilot.tools.inputs import Basis, CurrencyBasis


class ToolResult(BaseModel):
    """Common fields; the session fills ``tool_call_id`` and ``facts``."""

    tool_call_id: str = ""
    warnings: list[WarningCode] = []
    facts: list[Fact] = []


class EffectiveScope(BaseModel):
    """What a call actually covered, echoed back so answers can state it."""

    level: ScopeLevel
    company_codes: list[str]
    filters: dict[str, str] = {}
    periods: list[str] = []
    version: str | None = None
    actual: str | None = None
    comparison: str | None = None
    comparison_periods: list[str] | None = None
    currency_basis: CurrencyBasis | None = None
    basis: Basis | None = None


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str


class ToolErrorResult(BaseModel):
    tool_call_id: str
    error: ErrorDetail
