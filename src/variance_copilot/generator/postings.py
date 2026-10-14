"""Posting engine: proto-documents to journal headers and lines (SPEC §7.12, §8.7)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from variance_copilot.generator.fx import GROUP_CURRENCY
from variance_copilot.generator.master import CHART_OF_ACCOUNTS
from variance_copilot.generator.model import Entry, Master, Rates

CENTS = Decimal("0.01")
MILLI = Decimal("0.001")
NUMBER_RANGES = {
    "SA": 10000000, "AF": 11000000, "AL": 12000000, "WA": 49000000, "WE": 50000000,
    "KR": 51000000, "RV": 90000000, "ZR": 91000000, "RI": 92000000,
}  # fmt: skip

HEADER_COLUMNS = (
    "company_code", "fiscal_year", "document_number", "document_type", "posting_date",
    "document_date", "period", "entry_ts", "created_by", "transaction_currency", "header_text",
    "reference", "reversal_document", "reversal_fiscal_year", "is_reversal", "source",
)  # fmt: skip
LINE_COLUMNS = (
    "ledger", "company_code", "fiscal_year", "document_number", "line_item", "period",
    "posting_date", "chart_of_accounts", "gl_account", "debit_credit", "amount_tc", "currency_tc",
    "amount_lc", "currency_lc", "amount_gc", "currency_gc", "quantity", "unit", "cost_center",
    "profit_center", "functional_area", "segment", "trading_partner", "material", "customer",
    "vendor", "wbs_element", "assignment", "line_text",
)  # fmt: skip

Row = dict[str, Any]


@dataclass
class Journal:
    headers: list[Row]
    lines: list[Row]
    event_documents: dict[str, list[str]] = field(default_factory=dict)


def cents(value: float | Decimal) -> Decimal:
    return Decimal(repr(value) if isinstance(value, float) else value).quantize(
        CENTS, rounding=ROUND_HALF_EVEN
    )


def _balance(amounts: list[Decimal]) -> list[Decimal]:
    """Put the rounding residual on the largest line so the document sums to zero."""
    residual = -sum(amounts, Decimal(0))
    if residual and amounts:
        largest = max(range(len(amounts)), key=lambda i: abs(amounts[i]))
        amounts[largest] += residual
    return amounts


def in_window(entry: Entry, as_of: date) -> bool:
    return entry.posting_date <= as_of and entry.entry_ts.date() <= as_of


def translate(entry: Entry, master: Master, rates: Rates) -> list[tuple[Decimal, Decimal, Decimal]]:
    """TC, LC and GC amounts per line at the AVG rate of the posting month (SPEC §8.6)."""
    local = master.currency(entry.company_code)
    month = entry.posting_date.replace(day=1)
    tc = _balance([cents(line.amount) for line in entry.lines])
    to_local = rates.to_usd("AVG", entry.currency, month) / rates.to_usd("AVG", local, month)
    lc = tc if entry.currency == local else _balance([cents(float(a) * to_local) for a in tc])
    to_group = rates.to_usd("AVG", local, month)
    gc = lc if local == GROUP_CURRENCY else _balance([cents(float(a) * to_group) for a in lc])
    return list(zip(tc, lc, gc, strict=True))


def post(entries: Iterable[Entry], master: Master, rates: Rates) -> Journal:
    kept = (
        replace(e, lines=tuple(line for line in e.lines if cents(line.amount))) for e in entries
    )
    ordered = sorted(
        (e for e in kept if e.lines),
        key=lambda e: (e.company_code, e.posting_date, e.entry_ts, e.document_type),
    )
    counters: dict[tuple[str, int, str], int] = defaultdict(int)
    accruals: dict[str, Row] = {}
    journal = Journal([], [])
    for entry in ordered:
        year = entry.fiscal_year
        counter = (entry.company_code, year, entry.document_type)
        counters[counter] += 1
        number = f"{NUMBER_RANGES[entry.document_type] + counters[counter]:010d}"
        header: Row = {
            "company_code": entry.company_code,
            "fiscal_year": year,
            "document_number": number,
            "document_type": entry.document_type,
            "posting_date": entry.posting_date,
            "document_date": entry.document_date or entry.posting_date,
            "period": entry.period,
            "entry_ts": entry.entry_ts,
            "created_by": entry.created_by,
            "transaction_currency": entry.currency,
            "header_text": entry.header_text,
            "reference": None,
            "reversal_document": None,
            "reversal_fiscal_year": None,
            "is_reversal": entry.reverses is not None,
            "source": "SEED",
        }
        if entry.accrual_key:
            accruals[entry.accrual_key] = header
        if entry.reverses:
            original = accruals[entry.reverses]
            header["reversal_document"] = original["document_number"]
            header["reversal_fiscal_year"] = original["fiscal_year"]
            original["reversal_document"] = number
            original["reversal_fiscal_year"] = year
        if entry.event_id:
            reference = f"{entry.company_code}/{year}/{number}"
            journal.event_documents.setdefault(entry.event_id, []).append(reference)
        journal.headers.append(header)
        journal.lines += _lines(entry, number, master, rates)
    return journal


def _lines(entry: Entry, number: str, master: Master, rates: Rates) -> list[Row]:
    local = master.currency(entry.company_code)
    rows = []
    amounts = translate(entry, master, rates)
    for item, (line, (tc, lc, gc)) in enumerate(zip(entry.lines, amounts, strict=True), 1):
        profit_center, functional_area, segment = master.derive(entry.company_code, line)
        sign = lc if lc else tc
        rows.append(
            {
                "ledger": "0L",
                "company_code": entry.company_code,
                "fiscal_year": entry.fiscal_year,
                "document_number": number,
                "line_item": item,
                "period": entry.period,
                "posting_date": entry.posting_date,
                "chart_of_accounts": CHART_OF_ACCOUNTS,
                "gl_account": line.gl_account,
                "debit_credit": "S" if sign >= 0 else "H",
                "amount_tc": tc,
                "currency_tc": entry.currency,
                "amount_lc": lc,
                "currency_lc": local,
                "amount_gc": gc,
                "currency_gc": GROUP_CURRENCY,
                "quantity": Decimal(repr(line.quantity)).quantize(MILLI) if line.quantity else None,
                "unit": "PK" if line.quantity else None,
                "cost_center": line.cost_center,
                "profit_center": profit_center,
                "functional_area": functional_area,
                "segment": segment,
                "trading_partner": line.trading_partner,
                "material": line.material,
                "customer": line.customer,
                "vendor": line.vendor,
                "wbs_element": line.wbs_element,
                "assignment": line.assignment,
                "line_text": line.text,
            }
        )
    return rows
