from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from botocore.awsrequest import AWSPreparedRequest, AWSResponse

from variance_copilot.agent.providers.base import Message, ProviderError, ToolCall, ToolSpec
from variance_copilot.agent.providers.bedrock import (
    RETRIES,
    BedrockProvider,
    completion,
    converse_request,
    supported_schema,
)
from variance_copilot.agent.providers.factory import make_llm_provider
from variance_copilot.agent.schemas import Answer
from variance_copilot.config import Settings

CLAUDE = "global.anthropic.claude-haiku-5-5"
NOVA = "eu.amazon.nova-lite-v1:0"
REGION = "eu-central-1"
TOOL = ToolSpec(
    "compare",
    "Variance table.",
    {"type": "object", "properties": {"company_code": {"type": "string"}}},
)
CALL = ToolCall("tooluse_1", "compare", {"company_code": "US10"})
CONVERSATION = [
    Message("system", "You are a test assistant."),
    Message("user", "Why did US10 net sales miss budget?"),
    Message("assistant", "", tool_calls=(CALL, ToolCall("tooluse_2", "compare", {}))),
    Message("tool", '{"facts": ["F1-1"]}', tool_call_id="tooluse_1"),
    Message("tool", '{"facts": ["F2-1"]}', tool_call_id="tooluse_2"),
]


def request(model_id: str = CLAUDE, **overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "messages": CONVERSATION,
        "tools": [TOOL],
        "response_schema": None,
        "temperature": 0.0,
        "max_output_tokens": 2000,
        **overrides,
    }
    return dict(converse_request(model_id, **args))


def response(
    content: list[dict[str, Any]], stop: str = "end_turn", request_id: str = "req-1"
) -> dict[str, Any]:
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop,
        "usage": {"inputTokens": 1200, "outputTokens": 85, "totalTokens": 1285},
        "metrics": {"latencyMs": 640},
        "ResponseMetadata": {"RequestId": request_id},
    }


# --- Request ---------------------------------------------------------------


def test_system_messages_become_the_system_field() -> None:
    sent = request()

    assert sent["system"] == [{"text": "You are a test assistant."}]
    assert [turn["role"] for turn in sent["messages"]] == ["user", "assistant", "user"]


def test_tool_calls_and_results_map_to_tool_use_blocks() -> None:
    user, assistant, results = request()["messages"]

    assert user["content"] == [{"text": "Why did US10 net sales miss budget?"}]
    assert assistant["content"] == [
        {"toolUse": {"toolUseId": "tooluse_1", "name": "compare", "input": CALL.arguments}},
        {"toolUse": {"toolUseId": "tooluse_2", "name": "compare", "input": {}}},
    ]
    assert results["content"] == [
        {"toolResult": {"toolUseId": "tooluse_1", "content": [{"text": '{"facts": ["F1-1"]}'}]}},
        {"toolResult": {"toolUseId": "tooluse_2", "content": [{"text": '{"facts": ["F2-1"]}'}]}},
    ]


def test_assistant_text_is_kept_before_its_tool_calls() -> None:
    messages = [Message("user", "Hi"), Message("assistant", "Checking.", tool_calls=(CALL,))]

    assistant = request(messages=messages)["messages"][1]

    assert assistant["content"][0] == {"text": "Checking."}
    assert "toolUse" in assistant["content"][1]


def test_a_tool_message_without_its_call_id_is_rejected() -> None:
    with pytest.raises(ProviderError, match="tool_call_id"):
        request(messages=[Message("user", "Hi"), Message("tool", "{}")])


def test_tools_become_the_tool_config() -> None:
    assert request()["toolConfig"] == {
        "tools": [
            {
                "toolSpec": {
                    "name": "compare",
                    "description": "Variance table.",
                    "inputSchema": {"json": TOOL.input_schema},
                }
            }
        ]
    }
    assert "toolConfig" not in request(tools=[])


def test_claude_gets_no_sampling_parameters_and_reasoning_off() -> None:
    sent = request(CLAUDE, temperature=0.0)

    assert sent["inferenceConfig"] == {"maxTokens": 2000}
    assert sent["additionalModelRequestFields"] == {"thinking": {"type": "disabled"}}


def test_other_models_get_the_requested_temperature() -> None:
    sent = request(NOVA, temperature=0.0)

    assert sent["inferenceConfig"] == {"maxTokens": 2000, "temperature": 0.0}
    assert "additionalModelRequestFields" not in sent


def test_the_response_schema_becomes_a_json_schema_output_format() -> None:
    schema = Answer.model_json_schema()

    text_format = request(response_schema=schema)["outputConfig"]["textFormat"]

    assert text_format["type"] == "json_schema"
    assert json.loads(text_format["structure"]["jsonSchema"]["schema"]) == supported_schema(schema)
    assert "outputConfig" not in request()


def test_unsupported_schema_keywords_are_removed_everywhere() -> None:
    sent = supported_schema(Answer.model_json_schema())

    text = json.dumps(sent)
    assert "maxLength" not in text
    assert "maxItems" not in text
    assert sent["$defs"]["Driver"]["properties"]["title"] == {"title": "Title", "type": "string"}


def test_property_names_that_match_keywords_are_kept() -> None:
    schema = {"type": "object", "properties": {"minimum": {"type": "integer", "minimum": 0}}}

    assert supported_schema(schema) == {
        "type": "object",
        "properties": {"minimum": {"type": "integer"}},
    }


# --- Response --------------------------------------------------------------


def test_text_and_tool_calls_are_read_from_the_response() -> None:
    content = [
        {"text": "Checking. "},
        {"toolUse": {"toolUseId": "tooluse_1", "name": "compare", "input": CALL.arguments}},
        {"text": "Done."},
    ]

    result = completion(CLAUDE, response(content, stop="tool_use"))  # type: ignore[arg-type]

    assert result.message == Message("assistant", "Checking. Done.", tool_calls=(CALL,))
    assert result.stop_reason == "tool_use"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (1200, 85)
    assert result.model_id == CLAUDE
    assert result.raw == {"stop_reason": "tool_use", "request_id": "req-1", "latency_ms": 640}


@pytest.mark.parametrize(
    ("bedrock", "ours"),
    [
        ("end_turn", "end_turn"),
        ("stop_sequence", "end_turn"),
        ("tool_use", "tool_use"),
        ("max_tokens", "max_tokens"),
        ("model_context_window_exceeded", "max_tokens"),
        ("guardrail_intervened", "refusal"),
        ("content_filtered", "refusal"),
    ],
)
def test_stop_reasons_map_to_the_interface(bedrock: str, ours: str) -> None:
    result = completion(CLAUDE, response([{"text": "{}"}], stop=bedrock))  # type: ignore[arg-type]

    assert result.stop_reason == ours


@pytest.mark.parametrize("stop", ["malformed_model_output", "malformed_tool_use"])
def test_malformed_output_is_a_provider_error(stop: str) -> None:
    with pytest.raises(ProviderError, match=stop):
        completion(CLAUDE, response([], stop=stop))  # type: ignore[arg-type]


# --- Through botocore ------------------------------------------------------


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def stream(self, **_: Any) -> Iterator[bytes]:
        yield self._data


class FakeBedrock:
    """Answers Converse calls at the HTTP layer, so serialization, parsing and retries run."""

    def __init__(self, provider: BedrockProvider, timeout_s: float, *replies: AWSResponse) -> None:
        self.requests: list[dict[str, Any]] = []
        self.urls: list[str] = []
        self._replies = list(replies)
        provider.client(timeout_s).meta.events.register(
            "before-send.bedrock-runtime.Converse", self._send
        )

    def _send(self, request: AWSPreparedRequest, **_: Any) -> AWSResponse:
        self.urls.append(request.url)
        assert isinstance(request.body, bytes)
        self.requests.append(json.loads(request.body))
        return self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]


def reply(status: int, body: dict[str, Any], error: str | None = None) -> AWSResponse:
    headers = {"content-type": "application/json", "x-amzn-RequestId": "req-9"}
    if error:
        headers["x-amzn-ErrorType"] = error
    return AWSResponse("https://bedrock.test", status, headers, _Body(json.dumps(body).encode()))


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> BedrockProvider:
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    session = boto3.Session(
        aws_access_key_id="testing",
        aws_secret_access_key="testing",  # noqa: S106 - fake credentials for signing only
        region_name=REGION,
    )
    return BedrockProvider(CLAUDE, region=REGION, session=session)


def call(provider: BedrockProvider) -> Any:
    return provider.complete(
        CONVERSATION,
        [TOOL],
        response_schema=None,
        temperature=0.0,
        max_output_tokens=2000,
        timeout_s=10.0,
    )


def test_a_call_sends_the_converse_request_and_returns_the_completion(
    provider: BedrockProvider,
) -> None:
    body = response([{"text": '{"headline": "x"}'}])
    del body["ResponseMetadata"]
    bedrock = FakeBedrock(provider, 10.0, reply(200, body))

    result = call(provider)

    assert bedrock.urls == [
        f"https://bedrock-runtime.{REGION}.amazonaws.com/model/{CLAUDE}/converse"
    ]
    sent = request()
    del sent["modelId"]
    assert bedrock.requests == [sent]
    assert result.message.content == '{"headline": "x"}'
    assert result.raw["request_id"] == "req-9"


def test_transient_errors_are_retried_three_times_then_fail(provider: BedrockProvider) -> None:
    bedrock = FakeBedrock(
        provider, 10.0, reply(503, {"message": "busy"}, "ServiceUnavailableException")
    )

    with pytest.raises(ProviderError, match="ServiceUnavailableException"):
        call(provider)

    assert len(bedrock.requests) == RETRIES + 1


def test_a_retry_that_succeeds_returns_the_completion(provider: BedrockProvider) -> None:
    ok = response([{"text": "{}"}])
    del ok["ResponseMetadata"]
    bedrock = FakeBedrock(
        provider, 10.0, reply(429, {"message": "slow down"}, "ThrottlingException"), reply(200, ok)
    )

    assert call(provider).stop_reason == "end_turn"
    assert len(bedrock.requests) == 2


def test_validation_errors_are_not_retried(provider: BedrockProvider) -> None:
    bedrock = FakeBedrock(provider, 10.0, reply(400, {"message": "bad"}, "ValidationException"))

    with pytest.raises(ProviderError, match="ValidationException"):
        call(provider)

    assert len(bedrock.requests) == 1


def test_each_timeout_gets_its_own_client(provider: BedrockProvider) -> None:
    client = provider.client(10.0)

    assert provider.client(10.0) is client
    assert provider.client(30.0) is not client
    assert client.meta.config.read_timeout == 10.0
    assert client.meta.config.retries == {"total_max_attempts": RETRIES + 1, "mode": "standard"}


# --- Factory ---------------------------------------------------------------


def test_the_factory_builds_bedrock_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [name for name in os.environ if name.startswith("VC_")]:
        monkeypatch.delenv(name)
    settings = Settings(_env_file=None, llm_provider="bedrock", llm_model=CLAUDE, aws_region=REGION)  # type: ignore[call-arg]

    provider = make_llm_provider(settings)

    assert isinstance(provider, BedrockProvider)
    assert (provider.model_id, provider.region) == (CLAUDE, REGION)
