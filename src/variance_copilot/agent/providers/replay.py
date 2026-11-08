"""Recorded LLM completions for deterministic runs (SPEC §19.5, ADR-007)."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from variance_copilot.agent.providers.base import (
    Completion,
    LLMProvider,
    Message,
    ProviderError,
    ToolSpec,
)

DEFAULT_CASSETTE_DIR = Path("eval/cassettes")
CASSETTE_FORMAT = 1

_COMPLETION = TypeAdapter(Completion)


class RecordingNotFoundError(ProviderError):
    """Replay found no recording for a request."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def request_payload(
    model_id: str,
    messages: list[Message],
    tools: list[ToolSpec],
    *,
    response_schema: dict[str, Any] | None,
    temperature: float,
    max_output_tokens: int,
) -> dict[str, Any]:
    """Everything that shapes the model's output; the timeout does not."""
    return {
        "model": model_id,
        "messages": [asdict(message) for message in messages],
        "tools": [asdict(tool) for tool in tools],
        "parameters": {
            "response_schema": response_schema,
            "temperature": float(temperature),
            "max_output_tokens": max_output_tokens,
        },
    }


def request_sha256(request: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(request)).hexdigest()


class CassetteStore:
    """One gzipped JSON file per request, named by its SHA-256."""

    def __init__(self, directory: Path = DEFAULT_CASSETTE_DIR) -> None:
        self.directory = directory

    def path(self, key: str) -> Path:
        return self.directory / f"{key}.json.gz"

    def save(self, request: dict[str, Any], completion: Completion) -> Completion:
        """Write the recording and return the completion exactly as replay will."""
        key = request_sha256(request)
        record = {
            "format": CASSETTE_FORMAT,
            "request_sha256": key,
            "request": request,
            "completion": _COMPLETION.dump_python(completion, mode="json"),
        }
        data = gzip.compress(canonical_json(record), mtime=0)
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, self.path(key))
        return self.load(request)

    def load(self, request: dict[str, Any]) -> Completion:
        key = request_sha256(request)
        path = self.path(key)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise RecordingNotFoundError(
                f"no recorded completion for LLM request {key} (model {request['model']}) in "
                f"{self.directory}. The prompt, tool schemas, parameters or model changed since "
                "recording. Re-record with a live provider (`vc eval run --mode record` or "
                "VC_REPLAY_MODE=record) and commit the new cassettes."
            ) from None
        try:
            record = json.loads(gzip.decompress(data))
            if record["format"] != CASSETTE_FORMAT or record["request_sha256"] != key:
                raise ValueError(f"expected format {CASSETTE_FORMAT} for request {key}")
            return _COMPLETION.validate_python(record["completion"])
        except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
            raise ProviderError(f"unreadable cassette {path}: {exc}") from exc


class ReplayProvider:
    """Returns recorded completions and never calls a model."""

    name = "replay"

    def __init__(self, model_id: str, cassettes: Path = DEFAULT_CASSETTE_DIR) -> None:
        self.model_id = model_id
        self._store = CassetteStore(cassettes)

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
        request = request_payload(
            self.model_id,
            messages,
            tools,
            response_schema=response_schema,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        return self._store.load(request)


class RecordingProvider:
    """Calls a live provider and records every completion."""

    def __init__(
        self, live: LLMProvider, model_id: str, cassettes: Path = DEFAULT_CASSETTE_DIR
    ) -> None:
        self.name = live.name
        self.model_id = model_id
        self._live = live
        self._store = CassetteStore(cassettes)

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
        completion = self._live.complete(
            messages,
            tools,
            response_schema=response_schema,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            timeout_s=timeout_s,
        )
        request = request_payload(
            self.model_id,
            messages,
            tools,
            response_schema=response_schema,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        return self._store.save(request, completion)
