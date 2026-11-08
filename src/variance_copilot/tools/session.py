"""Tool definitions, the registry and the per-run session that invokes them (SPEC §10.1)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from psycopg.errors import QueryCanceled
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import DBAPIError

from variance_copilot.config import MaterialityConfig
from variance_copilot.finance.codes import ErrorCode, FinanceError
from variance_copilot.tools.context import (
    TOOL_TIMEOUT_SECONDS,
    Principal,
    ToolContext,
    ToolTimeoutError,
    TransactionFactory,
    deadline,
)
from variance_copilot.tools.facts import (
    FactLedger,
    FactRecorder,
    arguments_sha256,
    tool_call_id,
)
from variance_copilot.tools.results import ErrorDetail, ToolErrorResult, ToolResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tool[I: BaseModel, O: ToolResult]:
    name: str
    description: str
    input_model: type[I]
    output_model: type[O]
    function: Callable[[I, ToolContext], O]


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool[Any, Any]]) -> None:
        self._tools: dict[str, Tool[Any, Any]] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"tool {tool.name} is registered twice")
            self._tools[tool.name] = tool

    def get(self, name: str) -> Tool[Any, Any] | None:
        return self._tools.get(name)

    def __iter__(self) -> Iterator[Tool[Any, Any]]:
        return iter(sorted(self._tools.values(), key=lambda t: t.name))


def _validation_message(error: ValidationError) -> str:
    problems = error.errors(include_url=False, include_input=False)
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in problems)


def _timed_out(error: BaseException) -> bool:
    if isinstance(error, ToolTimeoutError):
        return True
    if isinstance(error, DBAPIError):
        return isinstance(error.orig, QueryCanceled) or isinstance(
            error.__cause__, ToolTimeoutError
        )
    return False


class ToolSession:
    """One run's tool calls, one at a time: numbering, one RLS transaction each, fact ledger."""

    def __init__(
        self,
        registry: ToolRegistry,
        principal: Principal,
        transactions: TransactionFactory,
        materiality: MaterialityConfig,
        *,
        run_id: UUID | None = None,
        timeout: float = TOOL_TIMEOUT_SECONDS,
    ) -> None:
        self.registry = registry
        self.principal = principal
        self.run_id = run_id or uuid4()
        self.ledger = FactLedger()
        self._transactions = transactions
        self._materiality = materiality
        self._timeout = timeout
        self._calls = 0
        self._lock = threading.Lock()

    def call(self, name: str, arguments: Mapping[str, Any]) -> ToolResult | ToolErrorResult:
        """Run a tool; failures come back as ``{error: {code, message}}``, never as exceptions."""
        with self._lock:
            return self._numbered_call(name, arguments)

    def _numbered_call(
        self, name: str, arguments: Mapping[str, Any]
    ) -> ToolResult | ToolErrorResult:
        self._calls += 1
        call_number = self._calls
        try:
            return self._call(name, arguments, call_number)
        except FinanceError as error:
            return self._error(call_number, error.code, error.message)
        except Exception as error:
            if _timed_out(error):
                return self._error(call_number, ErrorCode.TIMEOUT, "tool call timed out")
            logger.exception("tool %s failed", name)
            return self._error(call_number, ErrorCode.INTERNAL_ERROR, "tool call failed")

    def _call(self, name: str, arguments: Mapping[str, Any], call_number: int) -> ToolResult:
        tool = self.registry.get(name)
        if tool is None:
            raise FinanceError(ErrorCode.UNKNOWN_TOOL, f"unknown tool {name!r}")
        if not self.principal.has_finance_role or not self.principal.company_scope:
            raise FinanceError(ErrorCode.FORBIDDEN, "no finance role or company scope")
        try:
            params = tool.input_model.model_validate(arguments)
        except ValidationError as error:
            raise FinanceError(ErrorCode.INVALID_ARGUMENTS, _validation_message(error)) from error

        recorder = FactRecorder(call_number, tool.name, arguments_sha256(params))
        with (
            self._transactions(sorted(self.principal.company_scope)) as conn,
            deadline(conn, self._timeout),
        ):
            context = ToolContext(self.principal, self.run_id, conn, recorder, self._materiality)
            result: ToolResult = tool.function(params, context)
        facts = recorder.facts
        self.ledger.add(facts)
        return result.model_copy(update={"tool_call_id": recorder.tool_call_id, "facts": facts})

    @staticmethod
    def _error(call_number: int, code: ErrorCode, message: str) -> ToolErrorResult:
        return ToolErrorResult(
            tool_call_id=tool_call_id(call_number), error=ErrorDetail(code=code, message=message)
        )
