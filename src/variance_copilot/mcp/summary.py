"""One-line text summary of a tool result, for MCP clients that ignore structured content."""

from __future__ import annotations

from variance_copilot.tools.results import EffectiveScope, ToolErrorResult, ToolResult

COMMON_FIELDS = frozenset({"tool_call_id", "warnings", "facts"})


def _scope(scope: EffectiveScope) -> str:
    parts = [",".join(scope.company_codes) or scope.level.value]
    if scope.periods:
        parts.append("..".join(dict.fromkeys([scope.periods[0], scope.periods[-1]])))
    if scope.actual and scope.comparison:
        parts.append(f"{scope.actual} vs {scope.comparison}")
    elif scope.version:
        parts.append(scope.version)
    parts.extend(basis.value for basis in (scope.currency_basis, scope.basis) if basis)
    return " ".join(parts)


def summarize(tool: str, result: ToolResult | ToolErrorResult) -> str:
    """Counts, scope and warnings without any figure; the numbers stay in the facts."""
    if isinstance(result, ToolErrorResult):
        return f"{tool} {result.tool_call_id} failed: {result.error.code}: {result.error.message}"
    counts = [
        f"{name}={len(value)}"
        for name, value in result
        if name not in COMMON_FIELDS and isinstance(value, list)
    ]
    counts.append(f"facts={len(result.facts)}")
    parts = [f"{tool} {result.tool_call_id}: {', '.join(counts)}"]
    scope = getattr(result, "effective_scope", None)
    if isinstance(scope, EffectiveScope):
        parts.append(f"scope {_scope(scope)}")
    if result.warnings:
        parts.append(f"warnings {', '.join(result.warnings)}")
    return "; ".join(parts) + ". Figures are in the structured facts; cite them by fact ID."
