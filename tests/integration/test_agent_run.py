from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Connection, text

from variance_copilot.agent.events import MemoryEvents
from variance_copilot.agent.loop import Agent, DataInfo
from variance_copilot.agent.providers.base import Completion, Message, ToolCall, ToolSpec, Usage
from variance_copilot.agent.runs import PostgresRunStore
from variance_copilot.config import ModelPrice, Role
from variance_copilot.mcp.client import open_tool_client
from variance_copilot.tools.context import Principal
from variance_copilot.tools.session import ToolSession

pytestmark = pytest.mark.anyio

ANALYST_US = Principal("analyst_us", frozenset({Role.ANALYST}), frozenset({"US10"}))
COMPARE = {
    "scope": {"company_codes": ["US10"]},
    "period": {"fiscal_year": 2026, "period": 9, "view": "YTD"},
    "comparison": "BUD26",
}
USAGE = Usage(input_tokens=1000, output_tokens=100)


def app_savepoints(conn: Connection) -> Callable[[], AbstractContextManager[Connection]]:
    """``app.run`` writes as vc_app inside the seeded test transaction."""

    @contextmanager
    def transaction() -> Iterator[Connection]:
        with conn.begin_nested():
            conn.execute(text("SET LOCAL ROLE vc_app"))
            yield conn
            conn.execute(text("RESET ROLE"))

    return transaction


class CompareThenAnswer:
    """Calls ``compare`` once, then answers with the first amount fact it returned."""

    name = "scripted"

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        response_schema: dict[str, Any] | None,
        temperature: float,
        max_output_tokens: int,
        timeout_s: float,
    ) -> Completion:
        results = [m for m in messages if m.role == "tool"]
        if not results:
            call = ToolCall("call-1", "compare", COMPARE)
            return Completion(Message("assistant", "", (call,)), "tool_use", USAGE, "m")
        facts = json.loads(results[-1].content)["facts"]
        fact_id = next(f["fact_id"] for f in facts if f["kind"] == "AMOUNT")
        return Completion(Message("assistant", _answer(fact_id)), "end_turn", USAGE, "m")


def _answer(fact_id: str) -> str:
    return json.dumps(
        {
            "headline": f"US10 net sales differ from budget by {{{{{fact_id}}}}}.",
            "scope": {
                "company_codes": ["US10"],
                "period_label": "FY2026 P01-P09 YTD",
                "comparison": "BUD26",
                "currency_basis": "GC",
                "basis": "IFRS",
            },
            "drivers": [],
        }
    )


async def test_a_question_runs_through_mcp_and_records_the_run(
    ci_ledger: Connection, tool_session: Callable[[Connection, Principal], ToolSession]
) -> None:
    session = tool_session(ci_ledger, ANALYST_US)
    agent = Agent(
        provider=CompareThenAnswer(),
        model_id="test-model",
        price=ModelPrice(input_per_mtok=Decimal("1"), output_per_mtok=Decimal("5")),
        store=PostgresRunStore(app_savepoints(ci_ledger)),
        events=MemoryEvents(),
        data=DataInfo("test", "2026-10-09"),
    )

    async with open_tool_client(session) as client:
        result = await agent.ask(
            "Why are US10 net sales below budget?",
            ANALYST_US,
            client,
            session.ledger,
            run_id=session.run_id,
        )

    assert result.status == "SUCCEEDED", result.violations
    assert result.markdown is not None
    assert result.markdown.startswith("**US10 net sales differ from budget by ")
    assert "{{" not in result.markdown
    row = ci_ledger.execute(
        text(
            "SELECT status, requested_by, company_scope, prompt_version, model_provider, "
            "model_id, input_tokens, output_tokens, cost_usd, output->>'markdown' AS markdown, "
            "finished_at IS NOT NULL AS finished FROM app.run WHERE run_id = :run_id"
        ),
        {"run_id": session.run_id},
    ).one()
    assert row._asdict() == {
        "status": "SUCCEEDED",
        "requested_by": "analyst_us",
        "company_scope": ["US10"],
        "prompt_version": "qa@1",
        "model_provider": "scripted",
        "model_id": "test-model",
        "input_tokens": 2000,
        "output_tokens": 200,
        "cost_usd": Decimal("0.003000"),
        "markdown": result.markdown,
        "finished": True,
    }
