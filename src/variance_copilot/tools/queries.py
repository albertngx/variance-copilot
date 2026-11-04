"""Fixed, parameterised queries over the rpt views and master data; no SQL comes from input."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Connection,
    Select,
    TableClause,
    column,
    false,
    func,
    or_,
    select,
    table,
    tuple_,
)

from variance_copilot.finance.periods import Period
from variance_copilot.finance.versions import ResolvedVersion
from variance_copilot.tools.inputs import Dimension

_PNL_COLUMNS = (
    "company_code", "fiscal_year", "period", "pnl_line", "pnl_category", "functional_area",
    "gl_account", "segment", "brand", "cost_center", "profit_center", "material", "channel",
    "wbs_element", "is_intercompany", "core_adjustment", "amount_lc", "currency_lc", "amount_gc",
)  # fmt: skip
_LINE_COLUMNS = (
    "document_number", "line_item", "posting_date", "gl_account_name", "customer", "vendor",
    "trading_partner", "assignment", "line_text", "quantity",
)  # fmt: skip
_HEADER_COLUMNS = (
    "company_code", "fiscal_year", "document_number", "document_type", "document_date",
    "created_by", "header_text", "reference", "is_reversal", "reversal_document",
)  # fmt: skip

PNL_ACTUAL = table("pnl_actual", *map(column, _PNL_COLUMNS + _LINE_COLUMNS), schema="rpt")
PNL_PLAN = table("pnl_plan", column("plan_category"), *map(column, _PNL_COLUMNS), schema="rpt")
JOURNAL_HEADER = table("journal_header", *map(column, _HEADER_COLUMNS), schema="fin")
COMPANY_CODE = table("company_code", column("company_code"), column("local_currency"), schema="md")


@dataclass(frozen=True)
class PnlFilter:
    company_codes: tuple[str, ...]
    filters: dict[str, str]
    third_party_only: bool
    core_basis: bool


@dataclass(frozen=True)
class PnlCell:
    """Amounts for one row key, period and local currency."""

    key: tuple[str | None, ...]
    period: Period
    currency: str
    amount_lc: Decimal
    amount_gc: Decimal


def _scope_conditions(view: TableClause, scope: PnlFilter) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [view.c.company_code.in_(scope.company_codes)]
    conditions += [view.c[name] == value for name, value in scope.filters.items()]
    if scope.third_party_only:
        conditions.append(view.c.is_intercompany == false())
    if scope.core_basis:
        conditions.append(view.c.core_adjustment.is_(None))
    return conditions


def _in_periods(view: TableClause, periods: Sequence[Period]) -> ColumnElement[bool]:
    return tuple_(view.c.fiscal_year, view.c.period).in_(
        [(p.fiscal_year, p.period) for p in periods]
    )


def pnl_cells(
    conn: Connection, version: ResolvedVersion, scope: PnlFilter, dimensions: Sequence[Dimension]
) -> list[PnlCell]:
    """Presented P&L amounts of one version by the given dimensions, period and currency."""
    view = PNL_ACTUAL if version.is_actual else PNL_PLAN
    keys = [view.c[d.value] for d in dimensions if d is not Dimension.PERIOD]
    by = [*keys, view.c.fiscal_year, view.c.period, view.c.currency_lc]
    statement = (
        select(*by, func.sum(view.c.amount_lc), func.sum(view.c.amount_gc))
        .where(*_scope_conditions(view, scope), _in_periods(view, version.periods))
        .group_by(*by)
    )
    if not version.is_actual:
        statement = statement.where(view.c.plan_category == version.plan_category)
    cells = []
    for row in conn.execute(statement):
        *key, year, period, currency, amount_lc, amount_gc = row
        cells.append(PnlCell(tuple(key), Period(year, period), currency, amount_lc, amount_gc))
    return cells


def local_currencies(conn: Connection, company_codes: Sequence[str]) -> set[str]:
    statement = select(COMPANY_CODE.c.local_currency).where(
        COMPANY_CODE.c.company_code.in_(company_codes)
    )
    return set(conn.execute(statement).scalars())


@dataclass(frozen=True)
class LineFilter:
    gl_account: str | None = None
    cost_center: str | None = None
    material: str | None = None
    vendor: str | None = None
    document_type: str | None = None
    document_number: str | None = None
    text: str | None = None
    min_amount: Decimal | None = None


def _contains(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def journal_line_statement(
    scope: PnlFilter, periods: Sequence[Period], lines: LineFilter, limit: int
) -> Select[Any]:
    """P&L journal lines with their document header, largest group amounts first."""
    line, header = PNL_ACTUAL, JOURNAL_HEADER
    conditions = [*_scope_conditions(line, scope), _in_periods(line, periods)]
    exact = {
        line.c.gl_account: lines.gl_account,
        line.c.cost_center: lines.cost_center,
        line.c.material: lines.material,
        line.c.vendor: lines.vendor,
        header.c.document_type: lines.document_type,
        line.c.document_number: lines.document_number,
    }
    conditions += [col == value for col, value in exact.items() if value is not None]
    if lines.text is not None:
        pattern = _contains(lines.text)
        conditions.append(
            or_(
                line.c.line_text.ilike(pattern, escape="\\"),
                line.c.assignment.ilike(pattern, escape="\\"),
                header.c.header_text.ilike(pattern, escape="\\"),
            )
        )
    if lines.min_amount is not None:
        conditions.append(func.abs(line.c.amount_gc) >= lines.min_amount)
    return (
        select(
            *(line.c[c] for c in ("company_code", "fiscal_year", "period", *_LINE_COLUMNS)),
            *(line.c[c] for c in ("gl_account", "cost_center", "functional_area", "material")),
            *(line.c[c] for c in ("brand", "wbs_element", "amount_lc", "currency_lc", "amount_gc")),
            *(header.c[c] for c in _HEADER_COLUMNS[3:]),
        )
        .join_from(
            line,
            header,
            (header.c.company_code == line.c.company_code)
            & (header.c.fiscal_year == line.c.fiscal_year)
            & (header.c.document_number == line.c.document_number),
        )
        .where(*conditions)
        .order_by(
            func.abs(line.c.amount_gc).desc(),
            line.c.company_code,
            line.c.fiscal_year,
            line.c.document_number,
            line.c.line_item,
        )
        .limit(limit)
    )


@dataclass(frozen=True)
class Member:
    code: str
    name: str | None
    attributes: dict[str, str]


@dataclass(frozen=True)
class _MemberSource:
    table: TableClause
    code: str
    name: str | None
    attributes: tuple[str, ...]
    company_bound: bool = False
    where: tuple[tuple[str, tuple[str, ...]], ...] = ()


def _md(name: str, *columns: str) -> TableClause:
    return table(name, *map(column, columns), schema="md")


MEMBER_SOURCES = {
    "company_code": _MemberSource(
        _md("company_code", "company_code", "legal_name", "country_code", "local_currency"),
        "company_code", "legal_name", ("country_code", "local_currency"), company_bound=True,
    ),
    "segment": _MemberSource(_md("segment", "segment", "name"), "segment", "name", ()),
    "functional_area": _MemberSource(
        _md("functional_area", "functional_area", "name", "pnl_line"),
        "functional_area", "name", ("pnl_line",),
    ),
    "profit_center": _MemberSource(
        _md("profit_center", "profit_center", "name", "segment", "therapeutic_area"),
        "profit_center", "name", ("segment", "therapeutic_area"),
    ),
    "cost_center": _MemberSource(
        _md("cost_center", "cost_center", "name", "company_code", "functional_area", "category"),
        "cost_center", "name", ("company_code", "functional_area", "category"),
        company_bound=True,
    ),
    "gl_account": _MemberSource(
        _md("gl_account", "gl_account", "name", "pnl_category", "core_adjustment", "account_type"),
        "gl_account", "name", ("pnl_category", "core_adjustment"),
        where=(("account_type", ("P", "S")),),
    ),
    "brand": _MemberSource(
        _md("brand", "brand", "therapeutic_area", "modality", "lifecycle_stage"),
        "brand", None, ("therapeutic_area", "modality", "lifecycle_stage"),
    ),
    "material": _MemberSource(
        _md("material", "material", "description", "brand", "strength"),
        "material", "description", ("brand", "strength"),
    ),
    "wbs_element": _MemberSource(
        _md("wbs_element", "wbs_element", "description", "company_code", "brand", "project_type",
            "phase"),
        "wbs_element", "description", ("company_code", "brand", "project_type", "phase"),
        company_bound=True,
    ),
}  # fmt: skip


def members(
    conn: Connection,
    dimension: str,
    company_codes: Sequence[str],
    text: str | None,
    limit: int,
) -> list[Member]:
    source = MEMBER_SOURCES[dimension]
    t = source.table
    code = t.c[source.code]
    name = t.c[source.name] if source.name else None
    columns = [code, *([name] if name is not None else []), *(t.c[a] for a in source.attributes)]
    statement = select(*columns).order_by(code).limit(limit)
    if source.company_bound:
        statement = statement.where(t.c.company_code.in_(company_codes))
    for col, allowed in source.where:
        statement = statement.where(t.c[col].in_(allowed))
    if text is not None:
        pattern = _contains(text)
        matches = [code.ilike(pattern, escape="\\")]
        if name is not None:
            matches.append(name.ilike(pattern, escape="\\"))
        statement = statement.where(or_(*matches))
    result = []
    for row in conn.execute(statement).mappings():
        attributes = {a: str(row[a]) for a in source.attributes if row[a] is not None}
        result.append(
            Member(row[source.code], row[source.name] if source.name else None, attributes)
        )
    return result
