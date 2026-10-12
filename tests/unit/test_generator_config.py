from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from variance_copilot.config import ConfigError
from variance_copilot.generator.config import load_profiles, load_scenario
from variance_copilot.generator.pipeline import prepare
from variance_copilot.generator.rng import module_rng

SCENARIO = Path("seeds/scenario/fy2025_fy2026.yaml")


def _write_scenario(tmp_path: Path, edit: object) -> Path:
    raw = yaml.safe_load(SCENARIO.read_text(encoding="utf-8"))
    edit(raw)
    path = tmp_path / "scenario.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_scenario_has_every_event() -> None:
    scenario = load_scenario()

    assert [event.id for event in scenario.events] == [
        "E01", "E02", "E03", "E04a", "E04b", "E05", "E06", "E07", "E08", "E09", "E10", "E11",
        "E12",
    ]  # fmt: skip


def test_profiles_match_spec() -> None:
    profiles = load_profiles().profiles

    assert profiles["ci"].company_codes == ["CH10", "ES10", "IE10", "US10"]
    assert profiles["ci"].events == ["E01", "E02", "E05", "E06", "E07"]
    assert profiles["ci"].fiscal_years == [2026]
    assert profiles["dev"].billing == "MONTHLY"
    assert profiles["full"].billing == "WEEKLY"
    scenario_events = {event.id for event in load_scenario().events}
    for profile in profiles.values():
        assert set(profile.events) <= scenario_events


def test_invalid_event_field_reports_its_path(tmp_path: Path) -> None:
    def break_write_off(raw: dict) -> None:
        raw["events"][5]["amount_lc"] = "-1"

    with pytest.raises(ConfigError, match=r"events\.5\.INVENTORY_WRITE_OFF\.amount_lc"):
        load_scenario(_write_scenario(tmp_path, break_write_off))


def test_unknown_event_type_is_rejected(tmp_path: Path) -> None:
    def rename_type(raw: dict) -> None:
        raw["events"][0]["type"] = "LOSS"

    with pytest.raises(ConfigError, match=r"events\.0"):
        load_scenario(_write_scenario(tmp_path, rename_type))


def test_calibration_targets_must_add_up(tmp_path: Path) -> None:
    def inflate(raw: dict) -> None:
        raw["calibration"]["brands"]["CARDIVEX"]["net_sales_musd"] = 2000

    with pytest.raises(ConfigError, match="CARDIVEX"):
        load_scenario(_write_scenario(tmp_path, inflate))


def test_unknown_profile() -> None:
    with pytest.raises(ValueError, match="unknown profile"):
        prepare("tiny")


def test_module_streams_depend_only_on_seed_and_name() -> None:
    first = module_rng(42, "fx").random(5)

    np.testing.assert_array_equal(module_rng(42, "fx").random(5), first)
    module_rng(42, "a_new_module").random(100)  # another module drawing changes nothing
    np.testing.assert_array_equal(module_rng(42, "fx").random(5), first)


def test_module_streams_differ_by_module_and_seed() -> None:
    fx = module_rng(42, "fx").random(5)

    assert not np.array_equal(module_rng(42, "postings").random(5), fx)
    assert not np.array_equal(module_rng(43, "fx").random(5), fx)
