"""Generated-data validation V-01 to V-10 (SPEC §8.13)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Connection, text

from variance_copilot.config import load_materiality
from variance_copilot.generator.config import Decoys, ScenarioConfig
from variance_copilot.generator.events import (
    ALL_COMPANIES,
    DECOY_EVENT,
    NET_SALES,
    OPEX,
    event_year,
)
from variance_copilot.generator.model import Master
from variance_copilot.generator.truth import (
    EventTruth,
    Frame,
    Frames,
    difference,
    event_truth,
    frames,
)

Status = Literal["PASS", "FAIL", "N/A"]
SAMPLE_ROWS = 5
IMPACT_TOLERANCE = Decimal("0.005")


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    description: str
    status: Status
    detail: str = ""
    samples: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Expectations:
    """What the checks compare the database against."""

    master: Master
    calibration_year: int | None
    truths: Mapping[str, EventTruth]
    without_event: Mapping[str, Frames]
    last_closed: int
    decoys_active: bool
    checksums: Mapping[str, str]
    regenerate: Callable[[], Mapping[str, str]]


def _rows(conn: Connection, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(row._mapping) for row in conn.execute(text(sql), params)]


def _sql_check(conn: Connection, check_id: str, description: str, sql: str) -> CheckResult:
    offending = _rows(conn, sql)
    if not offending:
        return CheckResult(check_id, description, "PASS")
    return CheckResult(
        check_id, description, "FAIL", f"{len(offending)} offending rows", offending[:SAMPLE_ROWS]
    )


SQL_CHECKS = {
    "V-01": (
        "rpt.unbalanced_documents is empty",
        "SELECT * FROM rpt.unbalanced_documents",
    ),
    "V-02": (
        "No document entered into a period after it closed",
        """
        SELECT h.company_code, h.fiscal_year, h.document_number, h.period, h.entry_ts,
               ps.status, ps.closed_at
        FROM fin.journal_header h
        LEFT JOIN fin.period_status ps USING (company_code, fiscal_year, period)
        WHERE ps.status IS NULL OR (ps.closed_at IS NOT NULL AND h.entry_ts > ps.closed_at)
        """,
    ),
    "V-03": (
        "Posting dates inside period boundaries; special periods dated 31 December",
        """
        SELECT h.company_code, h.fiscal_year, h.document_number, h.period, h.posting_date
        FROM fin.journal_header h
        JOIN md.fiscal_period fp USING (fiscal_year, period)
        WHERE h.posting_date NOT BETWEEN fp.start_date AND fp.end_date
           OR (fp.is_special AND h.posting_date <> make_date(h.fiscal_year, 12, 31))
        UNION ALL
        SELECT jl.company_code, jl.fiscal_year, jl.document_number, jl.period, jl.posting_date
        FROM fin.journal_line jl
        JOIN fin.journal_header h USING (company_code, fiscal_year, document_number)
        WHERE jl.posting_date <> h.posting_date OR jl.period <> h.period
        """,
    ),
    "V-04": (
        "Every reversal references an existing original in the same company code",
        """
        SELECT r.company_code, r.fiscal_year, r.document_number, r.reversal_document
        FROM fin.journal_header r
        LEFT JOIN fin.journal_header o
          ON o.company_code = r.company_code
         AND o.fiscal_year = r.reversal_fiscal_year
         AND o.document_number = r.reversal_document
        WHERE r.is_reversal
          AND (o.document_number IS NULL OR o.reversal_document IS DISTINCT FROM r.document_number)
        """,
    ),
    "V-05": (
        "Intercompany revenue equals buyer expense per seller, buyer and period within USD 1",
        """
        WITH revenue AS (
            SELECT company_code AS seller, trading_partner AS buyer, fiscal_year, period,
                   -sum(amount_gc) AS amount
            FROM fin.journal_line WHERE gl_account IN ('40010000', '40600000')
            GROUP BY 1, 2, 3, 4),
        expense AS (
            SELECT trading_partner AS seller, company_code AS buyer, fiscal_year, period,
                   sum(amount_gc) AS amount
            FROM fin.journal_line WHERE gl_account IN ('50060000', '61090000')
            GROUP BY 1, 2, 3, 4)
        SELECT seller, buyer, fiscal_year, period,
               r.amount AS revenue, e.amount AS expense
        FROM revenue r FULL JOIN expense e USING (seller, buyer, fiscal_year, period)
        WHERE abs(coalesce(r.amount, 0) - coalesce(e.amount, 0)) > 1
        """,
    ),
    "V-06": (
        "Functional area follows the SPEC §7.4 derivation on every line",
        """
        SELECT jl.company_code, jl.fiscal_year, jl.document_number, jl.line_item,
               jl.gl_account, jl.cost_center, jl.functional_area
        FROM fin.journal_line jl
        JOIN md.gl_account ga USING (chart_of_accounts, gl_account)
        LEFT JOIN md.cost_center cc ON cc.cost_center = jl.cost_center
        WHERE jl.functional_area IS DISTINCT FROM
              CASE WHEN jl.cost_center IS NOT NULL THEN cc.functional_area
                   ELSE ga.default_functional_area END
        """,
    ),
}


def _calibration(conn: Connection, expected: Expectations, scenario: ScenarioConfig) -> CheckResult:
    description = "FY2025 net sales by brand and group within ±3%, FTE by company within ±5%"
    year = expected.calibration_year
    if year is None:
        return CheckResult("V-07", description, "N/A", "profile has no full FY2025 actuals")
    targets = scenario.calibration
    net_sales = {
        row["brand"]: Decimal(row["net_sales"])
        for row in _rows(
            conn,
            """
            SELECT brand, sum(amount_gc) AS net_sales FROM rpt.pnl_actual
            WHERE fiscal_year = :year AND period <= 12 AND NOT is_intercompany
              AND pnl_category IN ('GROSS_SALES', 'GTN_DEDUCTION')
            GROUP BY brand
            """,
            year=year,
        )
    }
    fte = {
        row["company_code"]: Decimal(row["fte"])
        for row in _rows(
            conn,
            """
            SELECT company_code, sum(value) / count(DISTINCT period) AS fte
            FROM fin.stat_key_figure
            WHERE version = 'ACT' AND key_figure = 'FTE' AND fiscal_year = :year
            GROUP BY company_code
            """,
            year=year,
        )
    }
    million = Decimal(1_000_000)
    checks = [
        (f"net sales {brand}", net_sales.get(brand, Decimal(0)), t.net_sales_musd * million,
         targets.tolerance)
        for brand, t in targets.brands.items()
    ]  # fmt: skip
    checks.append(
        (
            "group net sales",
            sum(net_sales.values(), Decimal(0)),
            targets.group_net_sales_musd * million,
            targets.tolerance,
        )
    )
    checks += [
        (f"FTE {c}", fte.get(c, Decimal(0)), t, targets.fte_tolerance)
        for c, t in targets.fte.items()
    ]
    misses = [
        {"measure": name, "target": str(target), "actual": str(actual.quantize(Decimal("0.01")))}
        for name, actual, target, tolerance in checks
        if abs(actual / target - 1) > tolerance
    ]
    if misses:
        return CheckResult("V-07", description, "FAIL", f"{len(misses)} off target", misses)
    return CheckResult("V-07", description, "PASS")


def _close(actual: Decimal, expected: Decimal) -> bool:
    return abs(actual - expected) <= max(abs(expected) * IMPACT_TOLERANCE, Decimal(1))


def _loaded(conn: Connection, master: Master) -> Frames:
    columns = (
        "company_code, fiscal_year, period, gl_account, functional_area, material,"
        " trading_partner, amount_lc, amount_gc"
    )
    lines = _rows(conn, f"SELECT {columns} FROM fin.journal_line")  # noqa: S608
    plans = _rows(conn, f"SELECT plan_category, {columns} FROM fin.plan_line")  # noqa: S608
    return frames(lines, plans, master)


def _event_impacts(
    conn: Connection, expected: Expectations, scenario: ScenarioConfig, loaded: Frames
) -> CheckResult:
    description = "Event impacts recomputed from the ledger match the manifest within 0.5%"
    if not expected.truths:
        return CheckResult("V-08", description, "N/A", "no events in profile")
    misses: list[dict[str, Any]] = []
    for event_id, truth in expected.truths.items():
        last_closed = expected.last_closed if truth.fiscal_year == scenario.as_of.year else 12
        recomputed = event_truth(
            scenario, event_id, loaded, expected.without_event[event_id], truth.documents,
            truth.fiscal_year, last_closed,
        )  # fmt: skip
        for version, by_period in truth.by_version.items():
            got_periods = recomputed.by_version.get(version, {})
            for period in sorted(set(by_period) | set(got_periods)):
                got, want = got_periods.get(period, Decimal(0)), by_period.get(period, Decimal(0))
                if not _close(got, want):
                    misses.append({"event": event_id, "version": version, "period": period,
                                   "ledger": str(got), "manifest": str(want)})  # fmt: skip
        target, value = scenario.event(event_id).target, recomputed.target_value
        if target and value is not None and not target.low - 1 <= value <= target.high + 1:
            misses.append(
                {"event": event_id, "target": f"{target.low}..{target.high}", "ledger": str(value)}
            )
    if misses:
        return CheckResult(
            "V-08", description, "FAIL", f"{len(misses)} mismatches", misses[:SAMPLE_ROWS]
        )
    return CheckResult("V-08", description, "PASS")


def _decoys(expected: Expectations, scenario: ScenarioConfig, loaded: Frames) -> CheckResult:
    description = "Decoys stay below materiality; the stable brand stays within its band"
    if not expected.decoys_active:
        return CheckResult("V-09", description, "N/A", "E12 not in profile")
    event = scenario.event(DECOY_EVENT)
    assert isinstance(event, Decoys)  # noqa: S101
    floor = load_materiality().pnl_line.company_code.relative_floor
    budget_version = f"BUD{event_year(scenario, event) % 100}"
    budget = loaded.plans.get(budget_version, {})
    diff = difference(loaded.actual, expected.without_event[DECOY_EVENT].actual)

    def cells(frame: Frame) -> dict[tuple[str, int, str], Decimal]:
        totals: dict[tuple[str, int, str], Decimal] = defaultdict(Decimal)
        for key, (_, gc) in frame.items():
            if key[4] in OPEX and key[2] <= expected.last_closed and not key[7]:
                totals[key[0], key[2], key[4]] += gc
        return totals

    planned = cells(budget)
    misses: list[dict[str, Any]] = []
    year_to_date: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for cell, delta in cells(diff).items():
        year_to_date[cell[0], cell[2]] += delta
        relative = abs(delta / planned[cell]) if planned.get(cell) else Decimal(1)
        if abs(delta) > event.max_abs_usd + 1 or abs(delta) >= floor or relative >= event.max_rel:
            misses.append({"cell": cell, "delta": str(delta), "relative": str(relative)})
    absolute = load_materiality().pnl_line.company_code.absolute
    misses += [
        {"line": line, "ytd": str(delta)}
        for line, delta in year_to_date.items()
        if abs(delta) >= absolute
    ]

    def stable_net(frame: Frame, company_code: str | None) -> Decimal:
        return sum(
            (gc for key, (_, gc) in frame.items()
             if key[6] == event.stable_brand and key[5] in NET_SALES and not key[7]
             and key[2] <= expected.last_closed and key[1] == scenario.as_of.year
             and company_code in (None, key[0])),
            Decimal(0),
        )  # fmt: skip

    for company_code in (None, *ALL_COMPANIES):
        planned_net = stable_net(budget, company_code)
        if planned_net:
            ratio = stable_net(loaded.actual, company_code) / planned_net - 1
            if abs(ratio) > event.stable_tolerance:
                misses.append(
                    {
                        "brand": event.stable_brand,
                        "company": company_code or "group",
                        "vs_budget": str(ratio),
                    }
                )
    if misses:
        return CheckResult(
            "V-09", description, "FAIL", f"{len(misses)} exceed limits", misses[:SAMPLE_ROWS]
        )
    return CheckResult("V-09", description, "PASS")


def _determinism(expected: Expectations) -> CheckResult:
    description = "Regenerating with the same seed reproduces the manifest checksums"
    regenerated = expected.regenerate()
    differing = [
        {"table": table, "manifest": expected.checksums.get(table), "regenerated": digest}
        for table, digest in sorted(regenerated.items())
        if expected.checksums.get(table) != digest
    ]
    if differing:
        return CheckResult(
            "V-10", description, "FAIL", f"{len(differing)} tables differ", differing
        )
    return CheckResult("V-10", description, "PASS")


def run_checks(
    conn: Connection, expected: Expectations, scenario: ScenarioConfig
) -> list[CheckResult]:
    results = [_sql_check(conn, check_id, *check) for check_id, check in SQL_CHECKS.items()]
    loaded = _loaded(conn, expected.master)
    results += [
        _calibration(conn, expected, scenario),
        _event_impacts(conn, expected, scenario, loaded),
        _decoys(expected, scenario, loaded),
        _determinism(expected),
    ]
    return results


def failures(results: list[CheckResult]) -> list[CheckResult]:
    return [result for result in results if result.status == "FAIL"]
