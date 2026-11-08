"""Reviewed JSON snapshots of public contracts."""

from __future__ import annotations

import difflib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

SNAPSHOTS = Path(__file__).parent / "snapshots"


@pytest.fixture
def snapshot(update_snapshots: bool) -> Callable[[str, Any], None]:
    """Compare a contract with ``snapshots/<name>.json``; rewrite it on --update-snapshots."""

    def check(name: str, served: Any) -> None:
        path = SNAPSHOTS / f"{name}.json"
        text = json.dumps(served, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if update_snapshots:
            SNAPSHOTS.mkdir(exist_ok=True)
            path.write_text(text, encoding="utf-8")

        reviewed = path.read_text(encoding="utf-8") if path.exists() else ""
        if text != reviewed:
            diff = difflib.unified_diff(
                reviewed.splitlines(keepends=True),
                text.splitlines(keepends=True),
                fromfile=f"{path.name} (reviewed)",
                tofile=f"{name} (served)",
            )
            pytest.fail(
                f"The {name} contract changed. Review the diff, then run `make snapshots` and "
                f"commit the snapshot with the change.\n\n{''.join(diff)}",
                pytrace=False,
            )

    return check
