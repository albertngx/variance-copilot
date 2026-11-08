"""Provider-neutral LLM and embedding interface (SPEC §12.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


StopReason = Literal["tool_use", "end_turn", "max_tokens", "refusal"]


@dataclass(frozen=True)
class Completion:
    message: Message
    stop_reason: StopReason
    usage: Usage
    model_id: str
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    name: str

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        response_schema: dict[str, Any] | None,
        temperature: float,
        max_output_tokens: int,
        timeout_s: float,
    ) -> Completion: ...


class EmbeddingProvider(Protocol):
    name: str
    dimension: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class ProviderError(Exception):
    """An LLM or embedding provider could not produce a result."""
