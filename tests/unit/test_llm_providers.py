from __future__ import annotations

import gzip
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from variance_copilot.agent.providers import factory
from variance_copilot.agent.providers.base import (
    Completion,
    LLMProvider,
    Message,
    ProviderError,
    ToolCall,
    ToolSpec,
    Usage,
)
from variance_copilot.agent.providers.factory import make_llm_provider
from variance_copilot.agent.providers.replay import (
    CassetteStore,
    RecordingNotFoundError,
    RecordingProvider,
    ReplayProvider,
    request_payload,
    request_sha256,
)
from variance_copilot.config import ConfigError, LLMProviderName, Settings

MODEL = "test-model"
MESSAGES = [
    Message("system", "You are a test assistant."),
    Message("user", "Why did US10 net sales miss budget?"),
]
TOOLS = [ToolSpec("compare", "Variance table.", {"type": "object", "properties": {}})]
SCHEMA = {"type": "object", "properties": {"headline": {"type": "string"}}}
PARAMS: dict[str, Any] = {"response_schema": SCHEMA, "temperature": 0.0, "max_output_tokens": 2000}

TOOL_TURN = Completion(
    message=Message(
        "assistant",
        "",
        tool_calls=(ToolCall("call-1", "compare", {"scope": {"company_codes": ["US10"]}}),),
    ),
    stop_reason="tool_use",
    usage=Usage(input_tokens=1200, output_tokens=85),
    model_id="test-model-2026-01-01",
    raw={"id": "msg_1", "stop_sequence": None},
)
FINAL_TURN = Completion(
    message=Message("assistant", '{"headline": "Net sales were {{F2-3|abs}} below budget."}'),
    stop_reason="end_turn",
    usage=Usage(input_tokens=2400, output_tokens=310),
    model_id="test-model-2026-01-01",
)


@dataclass
class ScriptedProvider:
    """Returns its completions in order and remembers each call's timeout."""

    completions: list[Completion]
    name: str = "scripted"
    timeouts: list[float] = field(default_factory=list)

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
        self.timeouts.append(timeout_s)
        return self.completions[len(self.timeouts) - 1]


def complete(provider: LLMProvider, *, timeout_s: float = 10.0, **overrides: Any) -> Completion:
    args: dict[str, Any] = {"messages": MESSAGES, "tools": TOOLS, **PARAMS, **overrides}
    return provider.complete(timeout_s=timeout_s, **args)


def request(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"messages": MESSAGES, "tools": TOOLS, **PARAMS, **overrides}
    return request_payload(args.pop("model", MODEL), **args)


def stored(directory: Path, key: str) -> dict[str, Any]:
    record: dict[str, Any] = json.loads(
        gzip.decompress((directory / f"{key}.json.gz").read_bytes())
    )
    return record


# --- Request key -----------------------------------------------------------


def test_request_key_is_the_sha256_of_canonical_json() -> None:
    payload = request()
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    assert set(payload) == {"model", "messages", "tools", "parameters"}
    assert request_sha256(payload) == hashlib.sha256(canonical.encode()).hexdigest()


def test_request_key_ignores_key_order_and_integer_temperature() -> None:
    reordered = {"properties": {"headline": {"type": "string"}}, "type": "object"}

    assert request_sha256(request(response_schema=reordered, temperature=0)) == request_sha256(
        request()
    )


@pytest.mark.parametrize(
    "change",
    [
        {"model": "other-model"},
        {"messages": [*MESSAGES, Message("user", "And versus forecast?")]},
        {"tools": [ToolSpec("compare", "Another description.", {"type": "object"})]},
        {"response_schema": None},
        {"temperature": 0.2},
        {"max_output_tokens": 1000},
    ],
    ids=["model", "messages", "tools", "response_schema", "temperature", "max_output_tokens"],
)
def test_request_key_changes_with_anything_that_shapes_the_output(change: dict[str, Any]) -> None:
    assert request_sha256(request(**change)) != request_sha256(request())


# --- Record and replay -----------------------------------------------------


def test_record_stores_gzipped_json_named_by_the_request_hash(tmp_path: Path) -> None:
    live = ScriptedProvider([TOOL_TURN])

    returned = complete(RecordingProvider(live, MODEL, tmp_path))

    key = request_sha256(request())
    record = stored(tmp_path, key)
    assert returned == TOOL_TURN
    assert record["request_sha256"] == key
    assert record["request"] == json.loads(json.dumps(request()))
    assert [p.name for p in tmp_path.iterdir()] == [f"{key}.json.gz"]


def test_replay_returns_the_recording_byte_for_byte(tmp_path: Path) -> None:
    recorded = complete(RecordingProvider(ScriptedProvider([TOOL_TURN]), MODEL, tmp_path))

    replayed = complete(ReplayProvider(MODEL, tmp_path))

    as_json = TypeAdapter(Completion).dump_json(replayed)
    assert replayed == recorded
    assert replayed.message.tool_calls == TOOL_TURN.message.tool_calls
    assert json.loads(as_json) == stored(tmp_path, request_sha256(request()))["completion"]


def test_record_returns_what_replay_will_return(tmp_path: Path) -> None:
    live_turn = Completion(**{**TOOL_TURN.__dict__, "raw": {"ids": ("a", "b")}})

    recorded = complete(RecordingProvider(ScriptedProvider([live_turn]), MODEL, tmp_path))

    assert recorded.raw == {"ids": ["a", "b"]}
    assert complete(ReplayProvider(MODEL, tmp_path)) == recorded


def test_recording_the_same_completion_twice_writes_identical_bytes(tmp_path: Path) -> None:
    path = tmp_path / f"{request_sha256(request())}.json.gz"
    complete(RecordingProvider(ScriptedProvider([TOOL_TURN]), MODEL, tmp_path))
    first = path.read_bytes()

    complete(RecordingProvider(ScriptedProvider([TOOL_TURN]), MODEL, tmp_path))

    assert path.read_bytes() == first


def test_record_always_calls_the_live_model_and_overwrites(tmp_path: Path) -> None:
    live = ScriptedProvider([TOOL_TURN, FINAL_TURN])
    recorder = RecordingProvider(live, MODEL, tmp_path)

    complete(recorder)
    complete(recorder)

    assert len(live.timeouts) == 2
    assert complete(ReplayProvider(MODEL, tmp_path)) == FINAL_TURN


def test_the_timeout_reaches_the_live_model_but_not_the_key(tmp_path: Path) -> None:
    live = ScriptedProvider([TOOL_TURN])
    complete(RecordingProvider(live, MODEL, tmp_path), timeout_s=10.0)

    assert live.timeouts == [10.0]
    assert complete(ReplayProvider(MODEL, tmp_path), timeout_s=30.0) == TOOL_TURN


def test_missing_recording_fails_with_the_hash_and_how_to_record(tmp_path: Path) -> None:
    key = request_sha256(request())

    with pytest.raises(RecordingNotFoundError, match=key) as excinfo:
        complete(ReplayProvider(MODEL, tmp_path))

    assert "vc eval run --mode record" in str(excinfo.value)
    assert "VC_REPLAY_MODE=record" in str(excinfo.value)


def test_replay_with_another_model_is_a_miss(tmp_path: Path) -> None:
    complete(RecordingProvider(ScriptedProvider([TOOL_TURN]), MODEL, tmp_path))

    with pytest.raises(RecordingNotFoundError):
        complete(ReplayProvider("other-model", tmp_path))


def test_a_corrupt_cassette_is_a_provider_error(tmp_path: Path) -> None:
    (tmp_path / f"{request_sha256(request())}.json.gz").write_bytes(b"not gzip")

    with pytest.raises(ProviderError, match="unreadable cassette"):
        complete(ReplayProvider(MODEL, tmp_path))


def test_a_cassette_under_the_wrong_name_is_rejected(tmp_path: Path) -> None:
    store = CassetteStore(tmp_path)
    store.save(request(temperature=0.5), TOOL_TURN)
    os.replace(
        store.path(request_sha256(request(temperature=0.5))), store.path(request_sha256(request()))
    )

    with pytest.raises(ProviderError, match="unreadable cassette"):
        store.load(request())


# --- Provider selection ----------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("VC_"):
            monkeypatch.delenv(name)


def settings(**env: str) -> Settings:
    return Settings(_env_file=None, **{"llm_model": MODEL, **env})  # type: ignore[call-arg]


@pytest.fixture
def live_openai(monkeypatch: pytest.MonkeyPatch) -> ScriptedProvider:
    live = ScriptedProvider([TOOL_TURN], name="openai")
    monkeypatch.setitem(factory.LIVE_PROVIDERS, LLMProviderName.OPENAI, lambda _s, _m: live)
    return live


@pytest.mark.parametrize(
    "env",
    [{"llm_provider": "replay"}, {"llm_provider": "openai", "replay_mode": "replay"}],
    ids=["VC_LLM_PROVIDER", "VC_REPLAY_MODE"],
)
def test_replay_is_selected_by_either_setting(env: dict[str, str], tmp_path: Path) -> None:
    provider = make_llm_provider(settings(**env), cassettes=tmp_path)

    assert isinstance(provider, ReplayProvider)
    assert provider.name == "replay"
    assert provider.model_id == MODEL


def test_live_provider_is_used_directly_when_replay_is_off(live_openai: ScriptedProvider) -> None:
    assert make_llm_provider(settings(llm_provider="openai")) is live_openai


def test_record_mode_wraps_the_live_provider(live_openai: ScriptedProvider, tmp_path: Path) -> None:
    provider = make_llm_provider(
        settings(llm_provider="openai", replay_mode="record"), cassettes=tmp_path
    )

    assert isinstance(provider, RecordingProvider)
    assert provider.name == "openai"
    assert complete(provider) == TOOL_TURN
    assert complete(ReplayProvider(MODEL, tmp_path)) == TOOL_TURN


def test_a_provider_without_an_implementation_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="VC_LLM_PROVIDER=bedrock is not available; available"):
        make_llm_provider(settings(llm_provider="bedrock"))


def test_the_model_id_is_required() -> None:
    with pytest.raises(ConfigError, match="VC_LLM_MODEL is required"):
        make_llm_provider(settings(llm_provider="replay", llm_model=""))
