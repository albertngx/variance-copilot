from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from variance_copilot.agent.prompts import load_prompt
from variance_copilot.agent.schemas import RESPONSE_MODELS

PROMPTS_DIR = Path(__file__).parents[2] / "prompts"
PROMPTS_SNAPSHOT = Path(__file__).parent / "snapshots" / "prompts.json"


def test_output_schemas_match_the_reviewed_snapshot(snapshot: Callable[[str, Any], None]) -> None:
    snapshot(
        "agent_output_schemas",
        {model.__name__: model.model_json_schema() for model in RESPONSE_MODELS},
    )


def _served_prompts() -> dict[str, dict[str, Any]]:
    names = sorted(
        p.relative_to(PROMPTS_DIR).with_suffix("").as_posix() for p in PROMPTS_DIR.rglob("*.md")
    )
    prompts = {name: load_prompt(name, PROMPTS_DIR) for name in names}
    return {
        name: {"id": p.meta.id, "version": p.meta.version, "sha256": p.sha256}
        for name, p in prompts.items()
    }


def test_prompt_versions_match_the_reviewed_snapshot(snapshot: Callable[[str, Any], None]) -> None:
    served = _served_prompts()
    reviewed = json.loads(PROMPTS_SNAPSHOT.read_text()) if PROMPTS_SNAPSHOT.exists() else {}

    unversioned = [
        name
        for name, entry in served.items()
        if name in reviewed
        and reviewed[name]["version"] == entry["version"]
        and reviewed[name]["sha256"] != entry["sha256"]
    ]
    assert not unversioned, (
        f"prompt text changed without a new version: {unversioned}. Bump `version` and add a "
        "changelog entry in the front matter, then run `make snapshots`."
    )
    snapshot("prompts", served)
