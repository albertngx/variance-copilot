"""Ground truth: each event's impact, isolated against a run without it (SPEC §8.8, §8.11)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from variance_copilot.generator.config import ScenarioConfig
from variance_copilot.generator.events import Measure, measure
from variance_copilot.generator.model import Master
from variance_copilot.generator.postings import Journal, Row

# company, year, period, gl account, P&L line, P&L category, brand, intercompany
PnlKey = tuple[str, int, int, str, str, str, str | None, bool]
Frame = dict[PnlKey, tuple[Decimal, Decimal]]  # presented LC, GC
PNL_LINES = {
    "F010": "REVENUE", "F100": "COGS", "F200": "SELLING_MARKETING", "F300": "RESEARCH_DEVELOPMENT",
    "F400": "GENERAL_ADMIN", "F500": "OTHER_INCOME", "F600": "OTHER_EXPENSE",
}  # fmt: skip


@dataclass(frozen=True)
class Frames:
    """Presented P&L of the actuals and of each plan version."""

    actual: Frame
    plans: dict[str, Frame]


def _add(frame: dict[PnlKey, list[Decimal]], row: Row, master: Master) -> None:
    account = master.accounts[row["gl_account"]]
    if account.account_type == "X":
        return
    brand = master.brand_of(row["material"]).brand if row["material"] else None
    key: PnlKey = (
        row["company_code"], row["fiscal_year"], row["period"], row["gl_account"],
        PNL_LINES[row["functional_area"]], str(account.pnl_category), brand,
        row["trading_partner"] is not None,
    )  # fmt: skip
    frame[key][0] -= Decimal(row["amount_lc"])
    frame[key][1] -= Decimal(row["amount_gc"])


def _empty() -> dict[PnlKey, list[Decimal]]:
    return defaultdict(lambda: [Decimal(0), Decimal(0)])


def _frozen(frame: dict[PnlKey, list[Decimal]]) -> Frame:
    return {key: (lc, gc) for key, (lc, gc) in frame.items()}


def pnl_frame(lines: Iterable[Row], master: Master) -> Frame:
    """Journal lines in presentation sign (income positive), as rpt.pnl_actual shows them."""
    frame = _empty()
    for line in lines:
        _add(frame, line, master)
    return _frozen(frame)


def frames(lines: Iterable[Row], plan_rows: Iterable[Row], master: Master) -> Frames:
    plans: dict[str, dict[PnlKey, list[Decimal]]] = defaultdict(_empty)
    for row in plan_rows:
        _add(plans[row["plan_category"]], row, master)
    return Frames(pnl_frame(lines, master), {v: _frozen(f) for v, f in plans.items()})


def difference(
    full: Mapping[PnlKey, tuple[Decimal, Decimal]], base: Mapping[PnlKey, tuple[Decimal, Decimal]]
) -> Frame:
    zero = (Decimal(0), Decimal(0))
    result = {}
    for key in set(full) | set(base):
        lc = full.get(key, zero)[0] - base.get(key, zero)[0]
        gc = full.get(key, zero)[1] - base.get(key, zero)[1]
        if lc or gc:
            result[key] = (lc, gc)
    return result


def in_scope(key: PnlKey, scope: Measure) -> bool:
    company_code, _, _, _, pnl_line, category, brand, intercompany = key
    return (
        company_code in scope.company_codes
        and (not scope.pnl_lines or pnl_line in scope.pnl_lines)
        and (not scope.categories or category in scope.categories)
        and (scope.brand is None or brand == scope.brand)
        and not (scope.third_party_only and intercompany)
    )


def _by_period(diff: Frame, scope: Measure, year: int, last_closed: int) -> dict[int, Decimal]:
    totals: dict[int, Decimal] = defaultdict(Decimal)
    for key, (_, gc) in diff.items():
        if key[1] == year and key[2] <= last_closed and in_scope(key, scope):
            totals[key[2]] += gc
    return {p: v for p, v in totals.items() if v}


@dataclass(frozen=True)
class EventTruth:
    event_id: str
    event_type: str
    title: str
    classification: str
    recurrence: str
    company_codes: list[str]
    gl_accounts: list[str]
    brands: list[str]
    fiscal_year: int
    periods: list[int]
    by_version: dict[str, dict[int, Decimal]]  # variance vs version caused by the event, GC
    by_line: dict[str, Decimal]  # group P&L lines the event moved, YTD, GC, third party
    target_value: Decimal | None  # measured slice over the target periods, target currency
    documents: list[str]
    details: list[dict[str, Any]] = field(default_factory=list)

    def ytd(self, version: str) -> Decimal:
        return sum(self.by_version.get(version, {}).values(), Decimal(0))

    def impact_by_version(self) -> dict[str, float]:
        return {version: float(self.ytd(version)) for version in self.by_version}

    def as_json(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "title": self.title,
            "classification": self.classification,
            "recurrence": self.recurrence,
            "company_codes": self.company_codes,
            "gl_accounts": self.gl_accounts,
            "brands": self.brands,
            "fiscal_year": self.fiscal_year,
            "periods": self.periods,
            "impact": {
                version: {
                    "currency": "USD",
                    "ytd": str(self.ytd(version)),
                    "by_period": {str(p): str(v) for p, v in sorted(by_period.items())},
                }
                for version, by_period in self.by_version.items()
            },
            "by_pnl_line": {k: str(v) for k, v in sorted(self.by_line.items())},
            "target_value": None if self.target_value is None else str(self.target_value),
            "documents": self.documents,
            "details": self.details,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> EventTruth:
        target = data["target_value"]
        return cls(
            event_id=data["event_id"],
            event_type=data["event_type"],
            title=data["title"],
            classification=data["classification"],
            recurrence=data["recurrence"],
            company_codes=data["company_codes"],
            gl_accounts=data["gl_accounts"],
            brands=data["brands"],
            fiscal_year=data["fiscal_year"],
            periods=data["periods"],
            by_version={
                version: {int(p): Decimal(v) for p, v in impact["by_period"].items()}
                for version, impact in data["impact"].items()
            },
            by_line={k: Decimal(v) for k, v in data["by_pnl_line"].items()},
            target_value=None if target is None else Decimal(target),
            documents=data["documents"],
            details=data["details"],
        )


def event_truth(
    scenario: ScenarioConfig,
    event_id: str,
    full: Frames,
    without: Frames,
    documents: list[str],
    year: int,
    last_closed: int,
    details: list[dict[str, Any]] | None = None,
) -> EventTruth:
    """One event's isolated effect on actuals and on the variance against each version."""
    event = scenario.event(event_id)
    scope = measure(scenario, event_id)
    diff = difference(full.actual, without.actual)
    actual = _by_period(diff, scope, year, last_closed)
    by_version = {}
    for version in sorted(v for v in full.plans if _version_year(v) == year):
        planned = _by_period(
            difference(full.plans[version], without.plans.get(version, {})),
            scope,
            year,
            last_closed,
        )
        periods = set(actual) | set(planned)
        by_version[version] = {
            p: actual.get(p, Decimal(0)) - planned.get(p, Decimal(0)) for p in sorted(periods)
        }

    closed = {k: v for k, v in diff.items() if k[1] == year and k[2] <= last_closed}
    by_line: dict[str, Decimal] = defaultdict(Decimal)
    for key, (_, gc) in closed.items():
        if not key[7]:
            by_line[key[4]] += gc
    target = None
    if event.target:
        column = 1 if event.target.currency == "USD" else 0
        target = sum(
            (
                v[column]
                for k, v in closed.items()
                if in_scope(k, scope) and k[2] in event.target.periods
            ),
            Decimal(0),
        )
    moved = [k for k in closed if in_scope(k, scope)]
    return EventTruth(
        event_id=event_id,
        event_type=type(event).__name__,
        title=event.title,
        classification=event.classification,
        recurrence=event.recurrence,
        company_codes=sorted({k[0] for k in moved}),
        gl_accounts=sorted({k[3] for k in moved}),
        brands=sorted({k[6] for k in moved if k[6]}),
        fiscal_year=year,
        periods=sorted(actual),
        by_version=by_version,
        by_line={k: v for k, v in by_line.items() if v},
        target_value=target,
        documents=documents,
        details=details or [],
    )


def _version_year(version: str) -> int:
    return 2000 + int(version[-2:])


def affected_documents(journal: Journal, diff: Frame, scope: Measure, master: Master) -> list[str]:
    """Documents posting to the accounts, periods and brand an overlay event moved."""
    touched = {(k[0], k[1], k[2], k[3]) for k in diff if in_scope(k, scope)}
    documents = set()
    for line in journal.lines:
        where = (line["company_code"], line["fiscal_year"], line["period"], line["gl_account"])
        if where not in touched:
            continue
        brand = master.brand_of(line["material"]).brand if line["material"] else None
        if scope.brand is None or brand == scope.brand:
            documents.add(f"{line['company_code']}/{line['fiscal_year']}/{line['document_number']}")
    return sorted(documents)
