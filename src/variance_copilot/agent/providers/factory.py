"""Choose the LLM provider from ``VC_LLM_PROVIDER`` and ``VC_REPLAY_MODE`` (SPEC §12.2, §24)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from variance_copilot.agent.providers.base import LLMProvider
from variance_copilot.agent.providers.replay import (
    DEFAULT_CASSETTE_DIR,
    RecordingProvider,
    ReplayProvider,
)
from variance_copilot.config import ConfigError, LLMProviderName, ReplayMode, Settings

LiveProviderFactory = Callable[[Settings, str], LLMProvider]

LIVE_PROVIDERS: dict[LLMProviderName, LiveProviderFactory] = {}


def make_llm_provider(settings: Settings, *, cassettes: Path = DEFAULT_CASSETTE_DIR) -> LLMProvider:
    """Replay if either setting says so; otherwise the live provider, recorded on request."""
    model_id = settings.llm_model
    if not model_id:
        raise ConfigError("VC_LLM_MODEL is required: it pins the model and keys recordings")
    if settings.llm_provider is LLMProviderName.REPLAY or settings.replay_mode is ReplayMode.REPLAY:
        return ReplayProvider(model_id, cassettes)

    factory = LIVE_PROVIDERS.get(settings.llm_provider)
    if factory is None:
        available = ", ".join(sorted([LLMProviderName.REPLAY, *LIVE_PROVIDERS]))
        raise ConfigError(
            f"VC_LLM_PROVIDER={settings.llm_provider} is not available; available: {available}"
        )
    live = factory(settings, model_id)
    if settings.replay_mode is ReplayMode.RECORD:
        return RecordingProvider(live, model_id, cassettes)
    return live
