"""Amazon Bedrock Converse provider (SPEC §12.2, ADR-012)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from variance_copilot.agent.providers.base import (
    Completion,
    Message,
    ProviderError,
    StopReason,
    ToolCall,
    ToolSpec,
    Usage,
)

if TYPE_CHECKING:
    from mypy_boto3_bedrock_runtime import BedrockRuntimeClient
    from mypy_boto3_bedrock_runtime.literals import ConversationRoleType
    from mypy_boto3_bedrock_runtime.type_defs import (
        ContentBlockTypeDef,
        ConverseRequestTypeDef,
        ConverseResponseTypeDef,
        InferenceConfigurationTypeDef,
        MessageTypeDef,
        SystemContentBlockTypeDef,
        ToolTypeDef,
    )

RETRIES = 3
CONNECT_TIMEOUT_S = 5.0

# Claude structured outputs reject these keywords; the response model still enforces them.
UNSUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
        "multipleOf", "minItems", "maxItems",
    }
)  # fmt: skip

STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "stop_sequence": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "model_context_window_exceeded": "max_tokens",
    "guardrail_intervened": "refusal",
    "content_filtered": "refusal",
}


def is_claude(model_id: str) -> bool:
    return "anthropic.claude" in model_id


def converse_request(
    model_id: str,
    messages: list[Message],
    tools: list[ToolSpec],
    *,
    response_schema: dict[str, Any] | None,
    temperature: float,
    max_output_tokens: int,
) -> ConverseRequestTypeDef:
    """Converse arguments for one §12.2 request."""
    inference: InferenceConfigurationTypeDef = {"maxTokens": max_output_tokens}
    request: ConverseRequestTypeDef = {
        "modelId": model_id,
        "messages": _turns(message for message in messages if message.role != "system"),
        "inferenceConfig": inference,
    }
    system: list[SystemContentBlockTypeDef] = [
        {"text": message.content} for message in messages if message.role == "system"
    ]
    if system:
        request["system"] = system
    if is_claude(model_id):
        # ADR-012: no sampling parameters, reasoning off.
        request["additionalModelRequestFields"] = {"thinking": {"type": "disabled"}}
    else:
        inference["temperature"] = temperature
    if tools:
        request["toolConfig"] = {"tools": [_tool(tool) for tool in tools]}
    if response_schema is not None:
        schema = json.dumps(supported_schema(response_schema), sort_keys=True)
        request["outputConfig"] = {
            "textFormat": {"type": "json_schema", "structure": {"jsonSchema": {"schema": schema}}}
        }
    return request


def supported_schema(schema: Any) -> Any:
    """The JSON Schema without keywords Claude structured outputs reject."""
    if isinstance(schema, list):
        return [supported_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    return {
        key: _schema_map(value) if key in ("properties", "$defs") else supported_schema(value)
        for key, value in schema.items()
        if key not in UNSUPPORTED_SCHEMA_KEYWORDS
    }


def completion(model_id: str, response: ConverseResponseTypeDef) -> Completion:
    """A §12.2 completion from a Converse response."""
    reason = response["stopReason"]
    stop_reason = STOP_REASONS.get(reason)
    if stop_reason is None:
        raise ProviderError(f"{model_id} stopped with {reason}")
    output = response["output"].get("message")
    blocks = output["content"] if output else []
    uses = [block["toolUse"] for block in blocks if "toolUse" in block]
    calls = tuple(ToolCall(use["toolUseId"], use["name"], dict(use["input"])) for use in uses)
    text = "".join(block["text"] for block in blocks if "text" in block)
    usage = response["usage"]
    return Completion(
        message=Message("assistant", text, tool_calls=calls),
        stop_reason=stop_reason,
        usage=Usage(input_tokens=usage["inputTokens"], output_tokens=usage["outputTokens"]),
        model_id=model_id,
        raw={
            "stop_reason": reason,
            "request_id": response["ResponseMetadata"]["RequestId"],
            "latency_ms": response["metrics"]["latencyMs"],
        },
    )


class BedrockProvider:
    """Bedrock models through the Converse API, with three retries on transient errors."""

    name = "bedrock"

    def __init__(self, model_id: str, *, region: str, session: boto3.Session | None = None) -> None:
        self.model_id = model_id
        self.region = region
        self._session = session or boto3.Session(region_name=region)
        self._clients: dict[float, BedrockRuntimeClient] = {}

    def client(self, timeout_s: float) -> BedrockRuntimeClient:
        """The runtime client for one read timeout, created on first use."""
        if timeout_s not in self._clients:
            config = Config(
                connect_timeout=min(CONNECT_TIMEOUT_S, timeout_s),
                read_timeout=timeout_s,
                retries={"total_max_attempts": RETRIES + 1, "mode": "standard"},
            )
            self._clients[timeout_s] = self._session.client(
                "bedrock-runtime", region_name=self.region, config=config
            )
        return self._clients[timeout_s]

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
        request = converse_request(
            self.model_id,
            messages,
            tools,
            response_schema=response_schema,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        try:
            response = self.client(timeout_s).converse(**request)
        except (ClientError, BotoCoreError) as exc:
            raise ProviderError(f"Bedrock call to {self.model_id} failed: {exc}") from exc
        return completion(self.model_id, response)


def _turns(messages: Iterable[Message]) -> list[MessageTypeDef]:
    turns: list[tuple[ConversationRoleType, list[ContentBlockTypeDef]]] = []
    for message in messages:
        role, blocks = _blocks(message)
        if turns and turns[-1][0] == role:
            turns[-1][1].extend(blocks)
        else:
            turns.append((role, blocks))
    return [{"role": role, "content": blocks} for role, blocks in turns]


def _blocks(message: Message) -> tuple[ConversationRoleType, list[ContentBlockTypeDef]]:
    if message.role == "tool":
        call_id = message.tool_call_id
        if call_id is None:
            raise ProviderError("a tool message needs the tool_call_id it answers")
        result: ContentBlockTypeDef = {
            "toolResult": {"toolUseId": call_id, "content": [{"text": message.content}]}
        }
        return "user", [result]
    blocks: list[ContentBlockTypeDef] = [{"text": message.content}] if message.content else []
    blocks += [
        {"toolUse": {"toolUseId": call.call_id, "name": call.name, "input": call.arguments}}
        for call in message.tool_calls
    ]
    return ("assistant" if message.role == "assistant" else "user"), blocks


def _tool(tool: ToolSpec) -> ToolTypeDef:
    return {
        "toolSpec": {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": {"json": tool.input_schema},
        }
    }


def _schema_map(schemas: dict[str, Any]) -> dict[str, Any]:
    return {name: supported_schema(schema) for name, schema in schemas.items()}
