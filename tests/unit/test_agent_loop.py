from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from mcp import types

from variance_copilot.agent.events import MemoryEvents
from variance_copilot.agent.loop import (
    ANSWER_SCHEMA,
    Agent,
    DataInfo,
    Limits,
    Prompts,
    RunResult,
)
from variance_copilot.agent.providers.base import (
    Completion,
    Message,
    ProviderError,
    StopReason,
    ToolCall,
    ToolSpec,
    Usage,
)
from variance_copilot.agent.runs import RunEnd, RunStart
from variance_copilot.config import ModelPrice, Role
from variance_copilot.tools.context import Principal
from variance_copilot.tools.facts import Fact, FactKind, FactLedger, Provenance

pytestmark = pytest.mark.anyio

MODEL = "test-model"
ANALYST_US = Principal("analyst_us", frozenset({Role.ANALYST}), frozenset({"US10"}))
PRICE = ModelPrice(input_per_mtok=Decimal("1"), output_per_mtok=Decimal("5"))
USAGE = Usage(input_tokens=1000, output_tokens=100)
CALL_COST = Decimal("0.0015")
COMPARE_SCHEMA = {"type": "object", "properties": {"company_code": {"type": "string"}}}
DATA = DataInfo(version="data-v1", as_of="2026-10-09")

type Step = Completion | Callable[[list[Message]], Completion]


def completion(
    content: str = "", calls: tuple[ToolCall, ...] = (), stop: StopReason | None = None
) -> Completion:
    return Completion(
        message=Message("assistant", content, tool_calls=calls),
        stop_reason=stop or ("tool_use" if calls else "end_turn"),
        usage=USAGE,
        model_id=MODEL,
    )


def tools(*arguments: dict[str, Any]) -> Completion:
    calls = tuple(ToolCall(f"call-{n}", "compare", args) for n, args in enumerate(arguments, 1))
    return completion(calls=calls)


def answer(fact_id: str = "F1-1", modifier: str = "|abs") -> str:
    return json.dumps(
        {
            "headline": f"US10 net sales were {{{{{fact_id}{modifier}}}}} below budget.",
            "scope": {
                "company_codes": ["US10"],
                "period_label": "FY2026 P01-P09 YTD",
                "comparison": "BUD26",
                "currency_basis": "GC",
                "basis": "IFRS",
            },
            "drivers": [
                {
                    "title": "Lower CARDIVEX volume",
                    "impact_fact_id": fact_id,
                    "classification": "VOLUME",
                    "narrative": "Volumes fell after the loss of exclusivity.",
                    "recurrence": "RECURRING",
                }
            ],
        }
    )


@dataclass
class ScriptedModel:
    """Plays its steps in order, repeating the last one; records every request."""

    steps: list[Step]
    name: str = "scripted"
    requests: list[dict[str, Any]] = field(default_factory=list)

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
        self.requests.append(
            {
                "messages": messages,
                "tools": tools,
                "response_schema": response_schema,
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
            }
        )
        step = self.steps[min(len(self.requests), len(self.steps)) - 1]
        return step if isinstance(step, Completion) else step(messages)


@dataclass
class FakeTools:
    """An MCP client with one tool, ``compare``, that registers one amount fact per call."""

    ledger: FactLedger = field(default_factory=FactLedger)
    calls: list[dict[str, Any] | None] = field(default_factory=list)
    fail_with: Exception | None = None

    async def list_tools(self) -> types.ListToolsResult:
        tool = types.Tool(
            name="compare", description="Variance table.", input_schema=COMPARE_SCHEMA
        )
        return types.ListToolsResult(tools=[tool])

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> types.CallToolResult:
        if self.fail_with:
            raise self.fail_with
        self.calls.append(arguments)
        number = len(self.calls)
        fact = Fact(
            fact_id=f"F{number}-1",
            kind=FactKind.AMOUNT,
            value=Decimal("-41200000"),
            unit="USD",
            label="Net sales variance",
            provenance=Provenance(
                tool_call_id=f"tc-{number}",
                tool=name,
                arguments_sha256="0" * 64,
                row_key="US10",
                field="variance",
            ),
        )
        self.ledger.add([fact])
        structured = {
            "tool_call_id": f"tc-{number}",
            "warnings": [],
            "facts": [fact.model_dump(mode="json")],
        }
        return types.CallToolResult(
            content=[types.TextContent(text="1 fact")], structured_content=structured
        )


@dataclass
class MemoryRunStore:
    started: list[RunStart] = field(default_factory=list)
    finished: list[tuple[UUID, RunEnd]] = field(default_factory=list)

    def start(self, run: RunStart) -> None:
        self.started.append(run)

    def finish(self, run_id: UUID, end: RunEnd) -> None:
        self.finished.append((run_id, end))


@dataclass
class Harness:
    model: ScriptedModel
    tools: FakeTools = field(default_factory=FakeTools)
    store: MemoryRunStore = field(default_factory=MemoryRunStore)
    events: MemoryEvents = field(default_factory=MemoryEvents)
    limits: Limits = field(default_factory=Limits)
    price: ModelPrice = PRICE

    async def ask(self, question: str = "Why are US10 net sales below budget?") -> RunResult:
        agent = Agent(
            provider=self.model,
            model_id=MODEL,
            price=self.price,
            store=self.store,
            events=self.events,
            data=DATA,
            limits=self.limits,
        )
        return await agent.ask(question, ANALYST_US, self.tools, self.tools.ledger, run_id=uuid4())

    def tool_messages(self, request: int = -1) -> list[Message]:
        return [m for m in self.model.requests[request]["messages"] if m.role == "tool"]


def harness(*steps: Step, **options: Any) -> Harness:
    return Harness(ScriptedModel(list(steps)), **options)


US10 = {"company_code": "US10"}


# --- Answering -------------------------------------------------------------


async def test_a_tool_turn_then_a_valid_answer_succeeds() -> None:
    run = harness(tools(US10), completion(answer()))

    result = await run.ask()

    assert result.status == "SUCCEEDED"
    assert result.answer is not None
    assert result.markdown is not None
    assert "41.2" in result.markdown
    assert (result.turns, result.tool_calls) == (2, 1)
    assert result.usage == Usage(2000, 200)
    assert result.cost_usd == 2 * CALL_COST


async def test_the_model_sees_the_listed_tools_and_pinned_parameters() -> None:
    run = harness(completion(answer()))

    await run.ask("Why are US10 net sales below budget?")

    request = run.model.requests[0]
    system, user = request["messages"]
    assert request["tools"] == [ToolSpec("compare", "Variance table.", COMPARE_SCHEMA)]
    assert request["response_schema"] == ANSWER_SCHEMA
    assert (request["temperature"], request["max_output_tokens"]) == (0.0, 2000)
    assert system == Message("system", Prompts.load().system.render().text)
    assert user.role == "user"
    assert "Why are US10 net sales below budget?" in user.content
    assert "US10" in user.content.split("Context")[1]
    assert "2026-10-09" in user.content


async def test_tool_calls_run_one_by_one_in_the_order_the_model_listed() -> None:
    first, second, third = {"company_code": "US10"}, {"company_code": "ES10"}, {"x": 1}
    run = harness(tools(first, second, third), completion(answer()))

    await run.ask()

    assert run.tools.calls == [first, second, third]
    results = run.tool_messages()
    assert [m.tool_call_id for m in results] == ["call-1", "call-2", "call-3"]
    assert [json.loads(m.content)["tool_call_id"] for m in results] == ["tc-1", "tc-2", "tc-3"]


async def test_a_turn_runs_at_most_four_tool_calls() -> None:
    calls = [{"company_code": f"C{n}"} for n in range(5)]
    run = harness(tools(*calls), completion(answer()))

    result = await run.ask()

    assert run.tools.calls == calls[:4]
    assert result.tool_calls == 4
    fifth = json.loads(run.tool_messages()[4].content)
    assert fifth["error"]["code"] == "TOOL_LIMIT"
    assert "at most 4 tool calls per turn" in fifth["error"]["message"]


async def test_tool_calls_stop_at_the_run_limit() -> None:
    run = harness(
        tools({"n": 1}),
        tools({"n": 2}),
        tools({"n": 3}),
        completion(answer()),
        limits=Limits(max_tool_calls=2),
    )

    result = await run.ask()

    assert result.status == "SUCCEEDED"
    assert result.tool_calls == 2
    third = json.loads(run.tool_messages()[2].content)
    assert "limit of 2 tool calls is reached" in third["error"]["message"]


async def test_a_repeated_call_is_answered_from_the_cache() -> None:
    run = harness(tools(US10), tools(US10), completion(answer()))

    result = await run.ask()

    assert run.tools.calls == [US10]
    assert result.tool_calls == 1
    first, second = run.tool_messages()
    assert first.content == second.content
    cached = [e.payload["cached"] for e in run.events.events if e.event_type == "TOOL_CALL"]
    assert cached == [False, True]


# --- Limits ----------------------------------------------------------------


async def test_the_turn_limit_fails_the_run() -> None:
    run = harness(lambda messages: tools({"n": len(messages)}), limits=Limits(max_tool_calls=99))

    result = await run.ask()

    assert result.status == "FAILED"
    assert result.turns == 8
    assert result.error == "no valid answer within 8 turns"


async def test_passing_the_cost_budget_aborts_the_run() -> None:
    run = harness(tools(US10), completion(answer()), limits=Limits(max_cost_usd=Decimal("0.001")))

    result = await run.ask()

    assert result.status == "ABORTED_BUDGET"
    assert result.turns == 1
    assert result.cost_usd == CALL_COST
    assert run.store.finished[0][1].status == "ABORTED_BUDGET"


@dataclass
class SlowModel(ScriptedModel):
    def complete(self, *args: Any, **kwargs: Any) -> Completion:
        time.sleep(0.5)
        return super().complete(*args, **kwargs)


async def test_a_run_past_the_wall_clock_limit_fails() -> None:
    run = Harness(SlowModel([completion(answer())]), limits=Limits(run_seconds=0.05))

    started = time.monotonic()
    result = await run.ask()

    assert time.monotonic() - started < 0.4
    assert result.status == "FAILED"
    assert result.error == "the run took longer than 0.05 s"


# --- Checks and repair -----------------------------------------------------


async def test_an_invalid_answer_gets_one_repair_turn() -> None:
    run = harness(tools(US10), completion(answer("F9-9")), completion(answer()))

    result = await run.ask()

    assert result.status == "SUCCEEDED"
    assert result.turns == 3
    repair = run.model.requests[2]["messages"][-1]
    assert repair.role == "user"
    assert "failed validation" in repair.content
    assert "- headline: no fact F9-9 in this run" in repair.content
    assert "REPAIR_REQUESTED" in run.events.types


async def test_an_answer_still_invalid_after_the_repair_fails_validation() -> None:
    run = harness(completion("not json"), completion(answer("F9-9")))

    result = await run.ask()

    assert result.status == "FAILED_VALIDATION"
    assert result.turns == 2
    assert result.answer is not None
    assert "no fact F9-9 in this run" in "; ".join(result.violations)


async def test_a_cut_off_answer_is_sent_back_for_repair() -> None:
    run = harness(completion('{"headline": "US10', stop="max_tokens"), completion("{}"))

    await run.ask()

    repair = run.model.requests[1]["messages"][-1].content
    assert "cut off at 2000 output tokens" in repair


async def test_a_refusal_fails_the_run() -> None:
    result = await harness(completion("", stop="refusal")).ask()

    assert (result.status, result.error) == ("FAILED", "the model declined to answer")


async def test_a_provider_error_fails_the_run() -> None:
    def fail(_messages: list[Message]) -> Completion:
        raise ProviderError("Bedrock call failed: ThrottlingException")

    result = await harness(fail).ask()

    assert result.status == "FAILED"
    assert result.error == "Bedrock call failed: ThrottlingException"


# --- Records ---------------------------------------------------------------


async def test_the_run_record_has_prompt_model_tokens_cost_and_output() -> None:
    run = harness(tools(US10), completion(answer()))

    result = await run.ask()

    (start,) = run.store.started
    ((run_id, end),) = run.store.finished
    assert run_id == start.run_id == result.run_id
    assert (start.kind, start.requested_by, start.company_scope) == (
        "QUESTION",
        "analyst_us",
        ("US10",),
    )
    assert (start.prompt_version, start.model_provider, start.model_id) == (
        "qa@1",
        "scripted",
        MODEL,
    )
    assert (end.status, end.input_tokens, end.output_tokens, end.cost_usd) == (
        "SUCCEEDED",
        2000,
        200,
        2 * CALL_COST,
    )
    assert end.output["answer"]["headline"].startswith("US10 net sales")
    assert end.output["markdown"] == result.markdown


async def test_events_follow_the_run_in_order() -> None:
    run = harness(tools(US10), completion(answer()))

    await run.ask()

    assert run.events.types == [
        "RUN_STARTED",
        "LLM_CALL",
        "TOOL_CALL",
        "FACTS_REGISTERED",
        "LLM_CALL",
        "VALIDATION",
        "RUN_COMPLETED",
    ]
    started = run.events.events[0].payload
    prompts = Prompts.load()
    assert started["prompts"]["qa"] == {"version": 1, "sha256": prompts.system.render().sha256}
    assert set(started["prompts"]) == {"qa", "question"}
    assert started["data_version"] == "data-v1"
    assert run.events.events[3].payload["facts"] == {"F1-1": "-41200000"}


async def test_an_unexpected_error_still_closes_the_run_record() -> None:
    run = harness(tools(US10), tools=FakeTools(fail_with=RuntimeError("socket closed")))

    with pytest.raises(RuntimeError, match="socket closed"):
        await run.ask()

    assert run.store.finished[0][1].status == "FAILED"
    assert run.store.finished[0][1].output["error"] == "internal error: RuntimeError"
    assert run.events.types[-1] == "RUN_FAILED"
