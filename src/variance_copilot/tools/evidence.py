"""Evidence tool: ``journal_lines`` (SPEC §10.3)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field

from variance_copilot.finance.codes import WarningCode
from variance_copilot.finance.periods import period_warnings, resolve_periods
from variance_copilot.finance.reference import load_period_statuses
from variance_copilot.finance.translation import GROUP_CURRENCY
from variance_copilot.tools.context import MAX_ROWS, ToolContext
from variance_copilot.tools.facts import FactKind, FactRef
from variance_copilot.tools.inputs import (
    Code,
    DocumentNumber,
    DocumentType,
    GlAccount,
    InputModel,
    PeriodInput,
    ScopeInput,
    SearchText,
)
from variance_copilot.tools.queries import LineFilter, PnlFilter, journal_line_statement
from variance_copilot.tools.results import EffectiveScope, ToolResult
from variance_copilot.tools.scope import resolve_scope


class JournalLinesInput(InputModel):
    scope: ScopeInput
    period: PeriodInput
    gl_account: GlAccount | None = None
    cost_center: Code | None = None
    material: Code | None = None
    vendor: Code | None = None
    document_type: DocumentType | None = None
    document_number: DocumentNumber | None = None
    text: SearchText | None = None
    min_amount: Annotated[Decimal, Field(ge=0)] | None = None
    limit: Annotated[int, Field(ge=1, le=MAX_ROWS)] = 50


class JournalLine(BaseModel):
    company_code: str
    fiscal_year: int
    period: int
    document_number: str
    line_item: int
    document_type: str
    posting_date: date
    document_date: date
    created_by: str
    gl_account: str
    gl_account_name: str
    functional_area: str | None
    cost_center: str | None
    material: str | None
    brand: str | None
    customer: str | None
    vendor: str | None
    wbs_element: str | None
    trading_partner: str | None
    assignment: str | None
    line_text: str | None
    header_text: str | None
    reference: str | None
    is_reversal: bool
    reversal_document: str | None
    currency_lc: str
    amount_gc: FactRef
    amount_lc: FactRef


class JournalLinesResult(ToolResult):
    effective_scope: EffectiveScope
    lines: list[JournalLine]


def journal_lines(params: JournalLinesInput, ctx: ToolContext) -> JournalLinesResult:
    scope = resolve_scope(params.scope, ctx.principal)
    periods = resolve_periods(params.period.selection())
    pnl_filter = PnlFilter(scope.company_codes, scope.filters, scope.third_party_only, False)
    line_filter = LineFilter(
        gl_account=params.gl_account,
        cost_center=params.cost_center,
        material=params.material,
        vendor=params.vendor,
        document_type=params.document_type,
        document_number=params.document_number,
        text=params.text,
        min_amount=params.min_amount,
    )
    statement = journal_line_statement(pnl_filter, periods, line_filter, params.limit + 1)
    found = ctx.conn.execute(statement).mappings().all()

    lines = []
    for row in found[: params.limit]:
        row_key = (
            f"company_code={row['company_code']}|document={row['fiscal_year']}/"
            f"{row['document_number']}|line={row['line_item']}"
        )
        document = f"{row['fiscal_year']}/{row['document_number']}"
        label = f"{row['company_code']} {document} line {row['line_item']}"
        fields = {
            k: row[k] for k in JournalLine.model_fields if k not in ("amount_gc", "amount_lc")
        }
        lines.append(
            JournalLine(
                **fields,
                amount_gc=ctx.facts.add(
                    FactKind.AMOUNT,
                    row["amount_gc"],
                    GROUP_CURRENCY,
                    f"Amount, {label}",
                    row_key=row_key,
                    field="amount_gc",
                ),
                amount_lc=ctx.facts.add(
                    FactKind.AMOUNT,
                    row["amount_lc"],
                    row["currency_lc"],
                    f"Amount in local currency, {label}",
                    row_key=row_key,
                    field="amount_lc",
                ),
            )
        )

    statuses = load_period_statuses(ctx.conn, scope.company_codes)
    warnings = [*scope.warnings, *period_warnings(periods, scope.company_codes, statuses)]
    warnings += [WarningCode.TRUNCATED] if len(found) > params.limit else []
    warnings += [] if lines else [WarningCode.NO_DATA]
    return JournalLinesResult(
        warnings=warnings,
        effective_scope=EffectiveScope(
            level=scope.level,
            company_codes=list(scope.company_codes),
            filters=scope.filters,
            periods=[str(periods[0]), str(periods[-1])],
        ),
        lines=lines,
    )
