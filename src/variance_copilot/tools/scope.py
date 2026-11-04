"""Effective scope: what the caller asked for, limited to what the principal may see."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from variance_copilot.finance.codes import ErrorCode, FinanceError, WarningCode
from variance_copilot.finance.materiality import ScopeLevel
from variance_copilot.tools.context import Principal
from variance_copilot.tools.inputs import ScopeInput


@dataclass(frozen=True)
class ResolvedScope:
    level: ScopeLevel
    company_codes: tuple[str, ...]
    filters: dict[str, str] = field(default_factory=dict)
    warnings: tuple[WarningCode, ...] = ()

    @property
    def third_party_only(self) -> bool:
        """Group figures exclude every line with a trading partner (I-06)."""
        return self.level is ScopeLevel.GROUP

    def label(self) -> str:
        entities = "Group" if self.level is ScopeLevel.GROUP else ", ".join(self.company_codes)
        return " ".join([entities, *(f"{k}={v}" for k, v in self.filters.items())])


def visible_companies(
    requested: Sequence[str], principal: Principal
) -> tuple[tuple[str, ...], tuple[WarningCode, ...]]:
    """Requested codes the principal may see (all of them if none requested)."""
    if not requested:
        return tuple(sorted(principal.company_scope)), ()
    visible = tuple(sorted(set(requested) & principal.company_scope))
    hidden = sorted(set(requested) - principal.company_scope)
    if not visible:
        raise FinanceError(ErrorCode.NO_ACCESS, f"no access to {', '.join(hidden)}")
    return visible, (WarningCode.PARTIAL_SCOPE,) if hidden else ()


def resolve_scope(scope: ScopeInput, principal: Principal) -> ResolvedScope:
    if scope.level is ScopeLevel.GROUP:
        if not principal.is_group_analyst:
            raise FinanceError(ErrorCode.FORBIDDEN, "group totals need the group_analyst role")
        if scope.company_codes:
            raise FinanceError(
                ErrorCode.INVALID_ARGUMENTS, "GROUP covers every company code; omit company_codes"
            )
        return ResolvedScope(
            ScopeLevel.GROUP, tuple(sorted(principal.company_scope)), scope.filters()
        )
    codes, warnings = visible_companies(scope.company_codes, principal)
    return ResolvedScope(ScopeLevel.COMPANY_CODE, codes, scope.filters(), warnings)
