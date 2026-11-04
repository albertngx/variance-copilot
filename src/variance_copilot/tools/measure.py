"""Measure tools: ``get_pnl`` and ``compare`` (SPEC §10.3, §10.4)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from variance_copilot.finance.codes import ErrorCode, FinanceError, WarningCode
from variance_copilot.finance.materiality import is_material
from variance_copilot.finance.periods import Period, period_warnings, resolve_periods
from variance_copilot.finance.precision import ZERO, with_finance_context
from variance_copilot.finance.reference import (
    load_fx_rates,
    load_period_statuses,
    load_plan_versions,
)
from variance_copilot.finance.translation import GROUP_CURRENCY, FxRates, LocalAmount, fx_split
from variance_copilot.finance.variance import Magnitude, Variance, variance
from variance_copilot.finance.versions import ResolvedVersion, resolve_version
from variance_copilot.tools.context import MAX_ROWS, ToolContext
from variance_copilot.tools.facts import FactKind, FactRef
from variance_copilot.tools.inputs import (
    Basis,
    CurrencyBasis,
    Dimension,
    InputModel,
    PeriodInput,
    ScopeInput,
    Version,
)
from variance_copilot.tools.queries import PnlCell, PnlFilter, local_currencies, pnl_cells
from variance_copilot.tools.results import EffectiveScope, ToolResult
from variance_copilot.tools.scope import ResolvedScope, resolve_scope

GroupBy = Annotated[tuple[Dimension, ...], Field(min_length=1, max_length=3)]
Key = tuple[str | None, ...]


class GetPnlInput(InputModel):
    scope: ScopeInput
    period: PeriodInput
    version: Version = Version.ACT
    currency_basis: Literal[CurrencyBasis.GC, CurrencyBasis.LC] = CurrencyBasis.GC
    basis: Basis = Basis.IFRS
    group_by: GroupBy = (Dimension.PNL_LINE,)


class CompareInput(InputModel):
    scope: ScopeInput
    period: PeriodInput
    actual: Version = Version.ACT
    comparison: Version
    currency_basis: CurrencyBasis = CurrencyBasis.GC
    basis: Basis = Basis.IFRS
    group_by: GroupBy = (Dimension.PNL_LINE,)


class PnlRow(BaseModel):
    key: dict[str, str | None]
    amount: FactRef


class CompareRow(BaseModel):
    key: dict[str, str | None]
    actual: FactRef
    comparison: FactRef
    delta: FactRef
    delta_pct: FactRef | None
    fx_effect: FactRef | None
    delta_operational: FactRef | None
    favorable: bool
    magnitude: Magnitude
    material: bool


class GetPnlResult(ToolResult):
    effective_scope: EffectiveScope
    rows: list[PnlRow]
    total: PnlRow | None


class CompareResult(ToolResult):
    effective_scope: EffectiveScope
    rows: list[CompareRow]
    total: CompareRow | None


@dataclass(frozen=True)
class _Keys:
    dimensions: tuple[Dimension, ...]

    def of(self, cell: PnlCell, version: ResolvedVersion) -> Key:
        """Row key; a period is shown as the requested period it corresponds to."""
        values = iter(cell.key)
        period = str(cell.period.shift_years(-version.year_offset))
        return tuple(period if d is Dimension.PERIOD else next(values) for d in self.dimensions)

    def text(self, key: Key | None) -> str:
        if key is None:
            return "total"
        pairs = zip(self.dimensions, key, strict=True)
        return "|".join(f"{d.value}={'' if v is None else v}" for d, v in pairs)

    def mapping(self, key: Key | None) -> dict[str, str | None]:
        return (
            {} if key is None else {d.value: v for d, v in zip(self.dimensions, key, strict=True)}
        )


def _ordered[T](items: Iterable[tuple[Key, Decimal, T]]) -> list[tuple[Key, Decimal, T]]:
    """Largest absolute amount first, then key (SPEC §10.1 rule 5)."""
    return sorted(items, key=lambda item: (-abs(item[1]), tuple(v or "" for v in item[0])))


@dataclass
class _Request:
    scope: ResolvedScope
    periods: tuple[Period, ...]
    pnl_filter: PnlFilter
    keys: _Keys
    label: str
    unit: str
    warnings: list[WarningCode] = field(default_factory=list)

    def finish(self, row_count: int) -> list[WarningCode]:
        truncated = [WarningCode.TRUNCATED] if row_count > MAX_ROWS else []
        empty = [] if row_count else [WarningCode.NO_DATA]
        return [*self.warnings, *truncated, *empty]


def _request(
    scope_in: ScopeInput,
    period_in: PeriodInput,
    basis: Basis,
    currency_basis: CurrencyBasis,
    group_by: tuple[Dimension, ...],
    ctx: ToolContext,
) -> _Request:
    scope = resolve_scope(scope_in, ctx.principal)
    unit = GROUP_CURRENCY
    if currency_basis is CurrencyBasis.LC:
        currencies = local_currencies(ctx.conn, scope.company_codes)
        if len(currencies) != 1:
            raise FinanceError(
                ErrorCode.MIXED_CURRENCIES, "LC needs company codes that share one local currency"
            )
        unit = currencies.pop()
    return _Request(
        scope=scope,
        periods=resolve_periods(period_in.selection()),
        pnl_filter=PnlFilter(
            scope.company_codes, scope.filters, scope.third_party_only, basis is Basis.CORE
        ),
        keys=_Keys(group_by),
        label=f"{scope.label()}, {period_in.label()}, {currency_basis}, {basis}",
        unit=unit,
        warnings=list(scope.warnings),
    )


def _resolve(requested: Version, request: _Request, ctx: ToolContext) -> ResolvedVersion:
    """Resolve a version; actuals in open or closing periods add ``OPEN_PERIOD``."""
    resolved = resolve_version(requested.value, request.periods, load_plan_versions(ctx.conn))
    if resolved.is_actual:
        statuses = load_period_statuses(ctx.conn, request.scope.company_codes)
        for warning in period_warnings(resolved.periods, request.scope.company_codes, statuses):
            if warning not in request.warnings:
                request.warnings.append(warning)
    return resolved


def _name(version: ResolvedVersion) -> str:
    return version.plan_category or version.requested


def _span(periods: Sequence[Period]) -> list[str]:
    return [str(periods[0]), str(periods[-1])]


@with_finance_context
def get_pnl(params: GetPnlInput, ctx: ToolContext) -> GetPnlResult:
    request = _request(
        params.scope, params.period, params.basis, params.currency_basis, params.group_by, ctx
    )
    version = _resolve(params.version, request, ctx)
    local = params.currency_basis is CurrencyBasis.LC

    amounts: dict[Key, Decimal] = defaultdict(lambda: ZERO)
    for cell in pnl_cells(ctx.conn, version, request.pnl_filter, params.group_by):
        amounts[request.keys.of(cell, version)] += cell.amount_lc if local else cell.amount_gc

    def row(key: Key | None, amount: Decimal) -> PnlRow:
        row_key = request.keys.text(key)
        label = f"{_name(version)}, {row_key}, {request.label}"
        fact = ctx.facts.add(
            FactKind.AMOUNT, amount, request.unit, label, row_key=row_key, field="amount"
        )
        return PnlRow(key=request.keys.mapping(key), amount=fact)

    ordered = _ordered((key, amount, None) for key, amount in amounts.items())
    return GetPnlResult(
        warnings=request.finish(len(ordered)),
        effective_scope=EffectiveScope(
            level=request.scope.level,
            company_codes=list(request.scope.company_codes),
            filters=request.scope.filters,
            periods=_span(request.periods),
            version=_name(version),
            currency_basis=params.currency_basis,
            basis=params.basis,
        ),
        rows=[row(key, amount) for key, amount, _ in ordered[:MAX_ROWS]],
        total=row(None, sum(amounts.values(), ZERO)) if amounts else None,
    )


@dataclass
class _Sides:
    """One row's actual amounts by period and currency, and its comparison totals."""

    actual: list[LocalAmount] = field(default_factory=list)
    comparison_lc: Decimal = ZERO
    comparison_gc: Decimal = ZERO

    def absorb(self, other: _Sides) -> None:
        self.actual.extend(other.actual)
        self.comparison_lc += other.comparison_lc
        self.comparison_gc += other.comparison_gc


@dataclass(frozen=True)
class _Comparison:
    shown: Variance
    group_currency: Variance
    fx_effect: Variance | None = None
    operational: Variance | None = None


def _compare(
    sides: _Sides, basis: CurrencyBasis, comparison: ResolvedVersion, rates: FxRates
) -> _Comparison:
    """Variance in the requested basis, the USD variance for materiality and the FX split."""
    actual_gc = sum((a.amount_gc for a in sides.actual), ZERO)
    group_currency = variance(actual_gc, sides.comparison_gc)
    if basis is CurrencyBasis.LC:
        actual_lc = sum((a.amount_lc for a in sides.actual), ZERO)
        return _Comparison(variance(actual_lc, sides.comparison_lc), group_currency)
    split = fx_split(sides.actual, sides.comparison_gc, comparison, rates)
    operational = variance(split.actual_cc, split.comparison_gc)
    return _Comparison(
        shown=group_currency if basis is CurrencyBasis.GC else operational,
        group_currency=group_currency,
        fx_effect=variance(split.actual_gc, split.actual_cc),
        operational=operational,
    )


@dataclass(frozen=True)
class _CompareRows:
    """Builds compare rows and registers a fact for every number in them."""

    ctx: ToolContext
    request: _Request
    actual: str
    comparison: str

    def row(self, key: Key | None, result: _Comparison) -> CompareRow:
        row_key = self.request.keys.text(key)
        versus = f"{self.actual} vs {self.comparison}"
        shown, fx, operational = result.shown, result.fx_effect, result.operational

        def fact(
            kind: FactKind, value: Decimal, measure: str, name: str, of: Variance | None = None
        ) -> FactRef:
            unit = "%" if kind is FactKind.PERCENT else self.request.unit
            label = f"{measure}, {row_key}, {self.request.label}"
            return self.ctx.facts.add(
                kind, value, unit, label, row_key=row_key, field=name, variance=of
            )

        pct = shown.delta_pct
        usd = shown if self.request.unit == GROUP_CURRENCY else result.group_currency
        return CompareRow(
            key=self.request.keys.mapping(key),
            actual=fact(FactKind.AMOUNT, shown.actual, self.actual, "actual"),
            comparison=fact(FactKind.AMOUNT, shown.comparison, self.comparison, "comparison"),
            delta=fact(FactKind.AMOUNT, shown.delta, f"Variance {versus}", "delta", shown),
            delta_pct=None
            if pct is None
            else fact(FactKind.PERCENT, pct, f"Variance % {versus}", "delta_pct", shown),
            fx_effect=None
            if fx is None
            else fact(
                FactKind.AMOUNT, fx.delta, f"FX effect at {self.comparison} rates", "fx_effect", fx
            ),
            delta_operational=None
            if operational is None
            else fact(
                FactKind.AMOUNT,
                operational.delta,
                f"Operational variance {versus}",
                "delta_operational",
                operational,
            ),
            favorable=shown.favorable,
            magnitude=shown.magnitude,
            material=is_material(usd, self.request.scope.level, self.ctx.materiality),
        )


@with_finance_context
def compare(params: CompareInput, ctx: ToolContext) -> CompareResult:
    if params.actual is Version.PY:
        raise FinanceError(ErrorCode.INVALID_ARGUMENTS, "actual must be ACT or a plan version")
    request = _request(
        params.scope, params.period, params.basis, params.currency_basis, params.group_by, ctx
    )
    actual = _resolve(params.actual, request, ctx)
    comparison = _resolve(params.comparison, request, ctx)
    rates = load_fx_rates(ctx.conn)

    sides: dict[Key, _Sides] = defaultdict(_Sides)
    for cell in pnl_cells(ctx.conn, actual, request.pnl_filter, params.group_by):
        amount = LocalAmount(cell.period, cell.currency, cell.amount_lc, cell.amount_gc)
        sides[request.keys.of(cell, actual)].actual.append(amount)
    for cell in pnl_cells(ctx.conn, comparison, request.pnl_filter, params.group_by):
        side = sides[request.keys.of(cell, comparison)]
        side.comparison_lc += cell.amount_lc
        side.comparison_gc += cell.amount_gc

    basis = params.currency_basis
    results = {key: _compare(side, basis, comparison, rates) for key, side in sides.items()}
    if any(WarningCode.NO_COMPARISON_BASE in r.shown.warnings for r in results.values()):
        request.warnings.append(WarningCode.NO_COMPARISON_BASE)
    ordered = _ordered((key, result.shown.delta, result) for key, result in results.items())
    builder = _CompareRows(ctx, request, _name(actual), _name(comparison))
    rows = [builder.row(key, result) for key, _, result in ordered[:MAX_ROWS]]
    total = None
    if sides:
        everything = _Sides()
        for side in sides.values():
            everything.absorb(side)
        total = builder.row(None, _compare(everything, basis, comparison, rates))
    return CompareResult(
        warnings=request.finish(len(ordered)),
        effective_scope=EffectiveScope(
            level=request.scope.level,
            company_codes=list(request.scope.company_codes),
            filters=request.scope.filters,
            periods=_span(request.periods),
            actual=_name(actual),
            comparison=_name(comparison),
            comparison_periods=_span(comparison.periods) if comparison.year_offset else None,
            currency_basis=basis,
            basis=params.basis,
        ),
        rows=rows,
        total=total,
    )
