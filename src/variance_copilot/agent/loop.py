"""The Q&A agent loop (SPEC §12.3, ADR-013)."""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from typing import Any, Protocol
from uuid import UUID

import anyio
from anyio import to_thread
from mcp import types

from variance_copilot.agent.events import RunEvents
from variance_copilot.agent.prompts import Prompt, load_prompt
from variance_copilot.agent.providers.base import (
    Completion,
    LLMProvider,
    Message,
    ProviderError,
    ToolCall,
    ToolSpec,
    Usage,
)
from variance_copilot.agent.providers.replay import canonical_json, request_payload, request_sha256
from variance_copilot.agent.runs import RunEnd, RunStart, RunStatus, RunStore, cost_usd
from variance_copilot.agent.schemas import Answer
from variance_copilot.agent.validation import CheckedAnswer, check_answer
from variance_copilot.config import ModelPrice, Settings
from variance_copilot.tools.context import Principal
from variance_copilot.tools.facts import FactLedger

ANSWER_SCHEMA = Answer.model_json_schema()
TEMPERATURE = 0.0
LIMIT_ERROR = "TOOL_LIMIT"


class ToolClient(Protocol):
    """The part of an MCP client session the loop uses."""

    async def list_tools(self) -> types.ListToolsResult: ...

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> types.CallToolResult: ...


@dataclass(frozen=True)
class Limits:
    """SPEC §12.3 limits for one Q&A run."""

    max_turns: int = 8
    max_tool_calls: int = 12
    max_calls_per_turn: int = 4
    run_seconds: float = 90.0
    max_output_tokens: int = 2000
    max_cost_usd: Decimal = Decimal("0.50")
    repairs: int = 1
    llm_timeout_s: float = 60.0

    @classmethod
    def from_settings(cls, settings: Settings) -> Limits:
        return cls(
            max_turns=settings.max_turns,
            max_tool_calls=settings.max_tool_calls,
            max_cost_usd=settings.max_cost_usd_per_run,
        )


@dataclass(frozen=True)
class DataInfo:
    """What the tools read: ``version`` keys the tool cache, ``as_of`` goes into the question."""

    version: str
    as_of: str


@dataclass(frozen=True)
class Prompts:
    system: Prompt
    question: Prompt
    repair: Prompt

    @classmethod
    def load(cls) -> Prompts:
        return cls(load_prompt("system/qa"), load_prompt("question"), load_prompt("repair"))


@dataclass(frozen=True)
class RunResult:
    run_id: UUID
    status: RunStatus
    answer: Answer | None
    markdown: str | None
    violations: tuple[str, ...]
    error: str | None
    turns: int
    tool_calls: int
    usage: Usage
    cost_usd: Decimal

    def as_json(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "status": self.status,
            "answer": self.answer.model_dump(mode="json") if self.answer else None,
            "violations": list(self.violations),
            "error": self.error,
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "usage": asdict(self.usage),
            "cost_usd": str(self.cost_usd),
        }


@dataclass(frozen=True)
class _Outcome:
    status: RunStatus
    checked: CheckedAnswer | None = None
    error: str | None = None


class _RunStoppedError(Exception):
    def __init__(self, outcome: _Outcome) -> None:
        super().__init__(outcome.error)
        self.outcome = outcome


@dataclass
class Agent:
    """Answers one question: model turns, tool calls through MCP, answer checks and limits."""

    provider: LLMProvider
    model_id: str
    price: ModelPrice
    store: RunStore
    events: RunEvents
    data: DataInfo
    limits: Limits = Limits()
    prompts: Prompts = field(default_factory=Prompts.load)

    async def ask(
        self,
        question: str,
        principal: Principal,
        tools: ToolClient,
        facts: FactLedger,
        *,
        run_id: UUID,
    ) -> RunResult:
        scope = tuple(sorted(principal.company_scope))
        system = self.prompts.system.render()
        user = self.prompts.question.render(
            question=question, company_codes=", ".join(scope), as_of=self.data.as_of
        )
        self.store.start(
            RunStart(
                run_id=run_id,
                kind="QUESTION",
                requested_by=principal.subject,
                company_scope=scope,
                prompt_version=f"{system.prompt_id}@{system.version}",
                model_provider=self.provider.name,
                model_id=self.model_id,
                started_at=datetime.now(UTC),
            )
        )
        self.events.emit(
            "RUN_STARTED",
            {
                "kind": "QUESTION",
                "requested_by": principal.subject,
                "company_scope": list(scope),
                "request": question,
                "prompts": {
                    p.prompt_id: {"version": p.version, "sha256": p.sha256} for p in (system, user)
                },
                "model": {"provider": self.provider.name, "model_id": self.model_id},
                "configuration_sha256": _sha256(
                    canonical_json({k: str(v) for k, v in asdict(self.limits).items()})
                ),
                "data_version": self.data.version,
            },
        )
        opening = [Message("system", system.text), Message("user", user.text)]
        run = _Run(self, run_id, tools, facts, opening)
        timeout = f"the run took longer than {self.limits.run_seconds:g} s"
        outcome = _Outcome("FAILED", error=timeout)
        try:
            with anyio.move_on_after(self.limits.run_seconds):
                outcome = await run.execute()
        except Exception as exc:
            self._finish(run, _Outcome("FAILED", error=f"internal error: {type(exc).__name__}"))
            raise
        return self._finish(run, outcome)

    def _finish(self, run: _Run, outcome: _Outcome) -> RunResult:
        checked = outcome.checked
        result = RunResult(
            run_id=run.run_id,
            status=outcome.status,
            answer=checked.answer if checked else None,
            markdown=checked.rendered.markdown if checked and checked.rendered else None,
            violations=checked.violations if checked else (),
            error=outcome.error,
            turns=run.turns,
            tool_calls=run.tool_calls,
            usage=run.usage,
            cost_usd=run.cost,
        )
        output = result.as_json() | {"markdown": result.markdown}
        self.store.finish(
            run.run_id,
            RunEnd(
                status=result.status,
                finished_at=datetime.now(UTC),
                input_tokens=run.usage.input_tokens,
                output_tokens=run.usage.output_tokens,
                cost_usd=run.cost,
                output=output,
            ),
        )
        self.events.emit(
            "RUN_COMPLETED" if result.status == "SUCCEEDED" else "RUN_FAILED",
            {
                "status": result.status,
                "output_sha256": _sha256(canonical_json(output)),
                "turns": run.turns,
                "tool_calls": run.tool_calls,
                "usage": asdict(run.usage),
                "cost_usd": str(run.cost),
            },
        )
        return result


@dataclass
class _Run:
    agent: Agent
    run_id: UUID
    tools: ToolClient
    facts: FactLedger
    messages: list[Message]
    turns: int = 0
    tool_calls: int = 0
    usage: Usage = field(default_factory=lambda: Usage(0, 0))
    cost: Decimal = Decimal(0)
    cache: dict[str, str] = field(default_factory=dict)

    @property
    def limits(self) -> Limits:
        return self.agent.limits

    async def execute(self) -> _Outcome:
        specs = [
            ToolSpec(tool.name, tool.description or "", tool.input_schema)
            for tool in (await self.tools.list_tools()).tools
        ]
        repairs_left = self.limits.repairs
        try:
            while self.turns < self.limits.max_turns:
                completion = await self._complete(specs)
                if completion.stop_reason == "refusal":
                    return _Outcome("FAILED", error="the model declined to answer")
                if completion.message.tool_calls:
                    await self._run_tools(completion.message)
                    continue
                checked = self._check(completion)
                if not checked.violations:
                    return _Outcome("SUCCEEDED", checked)
                if repairs_left == 0:
                    return _Outcome("FAILED_VALIDATION", checked)
                repairs_left -= 1
                self._request_repair(completion.message, checked.violations)
        except _RunStoppedError as stop:
            return stop.outcome
        return _Outcome("FAILED", error=f"no valid answer within {self.limits.max_turns} turns")

    async def _complete(self, specs: list[ToolSpec]) -> Completion:
        self.turns += 1
        messages = list(self.messages)
        parameters: dict[str, Any] = {
            "response_schema": ANSWER_SCHEMA,
            "temperature": TEMPERATURE,
            "max_output_tokens": self.limits.max_output_tokens,
        }
        complete = partial(
            self.agent.provider.complete,
            messages,
            specs,
            timeout_s=self.limits.llm_timeout_s,
            **parameters,
        )
        started = time.monotonic()
        try:
            completion = await to_thread.run_sync(complete, abandon_on_cancel=True)
        except ProviderError as exc:
            raise _RunStoppedError(_Outcome("FAILED", error=str(exc))) from exc
        cost = cost_usd(completion.usage, self.agent.price)
        self.usage = Usage(
            self.usage.input_tokens + completion.usage.input_tokens,
            self.usage.output_tokens + completion.usage.output_tokens,
        )
        self.cost += cost
        request = request_payload(self.agent.model_id, messages, specs, **parameters)
        self.agent.events.emit(
            "LLM_CALL",
            {
                "turn": self.turns,
                "provider": self.agent.provider.name,
                "model_id": self.agent.model_id,
                "parameters": request["parameters"] | {"response_schema": "Answer"},
                "request_sha256": request_sha256(request),
                "response_sha256": _sha256(canonical_json(asdict(completion.message))),
                "usage": asdict(completion.usage),
                "cost_usd": str(cost),
                "latency_ms": round((time.monotonic() - started) * 1000),
                "stop_reason": completion.stop_reason,
            },
        )
        if self.cost > self.limits.max_cost_usd:
            raise _RunStoppedError(
                _Outcome(
                    "ABORTED_BUDGET",
                    error=f"cost USD {self.cost} passed the run budget of "
                    f"USD {self.limits.max_cost_usd}",
                )
            )
        return completion

    async def _run_tools(self, message: Message) -> None:
        self.messages.append(message)
        for position, call in enumerate(message.tool_calls):
            content = await self._tool_result(call, position)
            self.messages.append(Message("tool", content, tool_call_id=call.call_id))

    async def _tool_result(self, call: ToolCall, position: int) -> str:
        if position >= self.limits.max_calls_per_turn:
            return _limit_error(
                f"at most {self.limits.max_calls_per_turn} tool calls per turn; "
                f"call {call.name} again in your next turn if you still need it"
            )
        key = canonical_json([call.name, call.arguments, self.agent.data.version]).decode()
        cached = self.cache.get(key)
        if cached is not None:
            self.agent.events.emit(
                "TOOL_CALL", {"tool": call.name, "arguments": call.arguments, "cached": True}
            )
            return cached
        if self.tool_calls >= self.limits.max_tool_calls:
            return _limit_error(
                f"this run's limit of {self.limits.max_tool_calls} tool calls is reached; "
                "answer with the facts you already have"
            )
        self.tool_calls += 1
        started = time.monotonic()
        result = await self.tools.call_tool(call.name, call.arguments)
        structured = result.structured_content or {}
        self.agent.events.emit(
            "TOOL_CALL",
            {
                "tool": call.name,
                "arguments": call.arguments,
                "cached": False,
                "tool_call_id": structured.get("tool_call_id"),
                "effective_scope": structured.get("effective_scope"),
                "warnings": structured.get("warnings", []),
                "error": structured.get("error"),
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
        facts = structured.get("facts") or []
        if facts:
            self.agent.events.emit(
                "FACTS_REGISTERED",
                {
                    "tool_call_id": structured.get("tool_call_id"),
                    "facts": {fact["fact_id"]: fact["value"] for fact in facts},
                },
            )
        content = _tool_content(result)
        if not result.is_error:
            self.cache[key] = content
        return content

    def _check(self, completion: Completion) -> CheckedAnswer:
        if completion.stop_reason == "max_tokens":
            checked = CheckedAnswer(
                None,
                None,
                (
                    f"the answer was cut off at {self.limits.max_output_tokens} output tokens; "
                    "return a shorter answer",
                ),
            )
        else:
            checked = check_answer(completion.message.content, self.facts, run_id=self.run_id)
        self.agent.events.emit(
            "VALIDATION",
            {
                "violations": list(checked.violations),
                "placeholders": checked.rendered.placeholders if checked.rendered else {},
            },
        )
        return checked

    def _request_repair(self, answer: Message, violations: tuple[str, ...]) -> None:
        self.agent.events.emit("REPAIR_REQUESTED", {"violations": list(violations)})
        repair = self.agent.prompts.repair.render(
            violations="\n".join(f"- {violation}" for violation in violations)
        )
        self.messages += [Message("assistant", answer.content), Message("user", repair.text)]


def _tool_content(result: types.CallToolResult) -> str:
    if result.structured_content is not None:
        return canonical_json(result.structured_content).decode()
    return "\n".join(block.text for block in result.content if isinstance(block, types.TextContent))


def _limit_error(message: str) -> str:
    return canonical_json({"error": {"code": LIMIT_ERROR, "message": message}}).decode()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
