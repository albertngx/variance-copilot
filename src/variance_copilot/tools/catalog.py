"""The tool catalogue (SPEC §10.3); names and descriptions are part of the public contract."""

from __future__ import annotations

from variance_copilot.tools.discover import (
    GetFxRatesInput,
    GetFxRatesResult,
    GetPeriodStatusInput,
    GetPeriodStatusResult,
    ListDimensionMembersInput,
    ListDimensionMembersResult,
    get_fx_rates,
    get_period_status,
    list_dimension_members,
)
from variance_copilot.tools.evidence import JournalLinesInput, JournalLinesResult, journal_lines
from variance_copilot.tools.measure import (
    CompareInput,
    CompareResult,
    GetPnlInput,
    GetPnlResult,
    compare,
    get_pnl,
)
from variance_copilot.tools.session import Tool, ToolRegistry

DATA_TOOLS = ToolRegistry(
    [
        Tool(
            "list_dimension_members",
            "Look up valid codes and names for a dimension (company codes, segments, functional "
            "areas, profit centers, cost centers, P&L accounts, brands, materials, WBS elements). "
            "Use it to turn names in a question into codes before calling other tools.",
            ListDimensionMembersInput,
            ListDimensionMembersResult,
            list_dimension_members,
        ),
        Tool(
            "get_period_status",
            "Whether fiscal periods are OPEN, CLOSING or CLOSED for each visible company code. "
            "Figures for periods that are not closed are preliminary.",
            GetPeriodStatusInput,
            GetPeriodStatusResult,
            get_period_status,
        ),
        Tool(
            "get_pnl",
            "Profit and loss amounts of one version (actuals, a budget or forecast, or the prior "
            "year) for a scope and period, grouped by up to three dimensions. Income is positive "
            "and expense negative. Every amount is a fact ID; cite it as a placeholder.",
            GetPnlInput,
            GetPnlResult,
            get_pnl,
        ),
        Tool(
            "compare",
            "Variance table: actual versus budget, forecast or prior year for a scope and period. "
            "Each row has fact IDs for actual, comparison, variance, variance %, FX effect and "
            "operational variance, plus favorable and material flags. A positive variance is "
            "favorable for every P&L line. Cite numbers only through their fact IDs.",
            CompareInput,
            CompareResult,
            compare,
        ),
        Tool(
            "journal_lines",
            "Evidence drill-down: P&L journal lines with document details, largest amounts first, "
            "filtered by account, cost center, material, vendor, document type, document number, "
            "text or minimum amount. At most 200 lines.",
            JournalLinesInput,
            JournalLinesResult,
            journal_lines,
        ),
        Tool(
            "get_fx_rates",
            "Exchange rates used for translation, in USD per unit of currency, by rate type "
            "(AVG monthly average, CLO closing, B25/B26 budget, F126/F226 forecast) and month.",
            GetFxRatesInput,
            GetFxRatesResult,
            get_fx_rates,
        ),
    ]
)
