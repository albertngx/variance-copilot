"""Shared test configuration: layer markers and Hypothesis profiles (SPEC §20)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings

LAYERS = ("unit", "property", "integration", "contract", "api", "security")
TESTS_DIR = Path(__file__).parent

# `ci` is deterministic: same examples on every run, no example database.
settings.register_profile(
    "ci",
    derandomize=True,
    database=None,
    deadline=None,
    max_examples=200,
    print_blob=True,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile("dev", max_examples=50)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test with its layer, taken from its folder under tests/."""
    for item in items:
        layer = item.path.relative_to(TESTS_DIR).parts[0]
        if layer in LAYERS:
            item.add_marker(getattr(pytest.mark, layer))
