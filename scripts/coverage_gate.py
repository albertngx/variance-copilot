"""Check line coverage against the per-package thresholds in pyproject.toml (SPEC §20).

Reads ``coverage.json`` (``coverage json``). Prints a table; exits 1 on a shortfall only
when ``[tool.variance-copilot.coverage] enforce = true`` or ``--enforce`` is passed.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

PACKAGE_ROOT = "src/variance_copilot/"


def _percent(covered: int, total: int) -> float:
    return 100.0 if total == 0 else 100.0 * covered / total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("coverage.json"))
    parser.add_argument("--enforce", action="store_true", help="fail on shortfall")
    args = parser.parse_args()

    config = tomllib.loads(Path("pyproject.toml").read_text())["tool"]["variance-copilot"][
        "coverage"
    ]
    enforce: bool = args.enforce or config["enforce"]
    report = json.loads(args.report.read_text())

    rows: list[tuple[str, float, float]] = [
        ("overall", report["totals"]["percent_covered"], float(config["overall"]))
    ]
    for package, threshold in config["packages"].items():
        prefix = f"{PACKAGE_ROOT}{package}/"
        files = [s for name, s in report["files"].items() if name.startswith(prefix)]
        covered = sum(f["summary"]["covered_lines"] for f in files)
        total = sum(f["summary"]["num_statements"] for f in files)
        rows.append((package, _percent(covered, total), float(threshold)))

    failed = False
    for name, actual, threshold in rows:
        ok = actual >= threshold
        failed |= not ok
        print(f"{name:<12} {actual:6.1f}%  (min {threshold:.0f}%)  {'ok' if ok else 'BELOW'}")

    if failed and not enforce:
        print("coverage thresholds not enforced yet (enforced from M2)")
    return 1 if failed and enforce else 0


if __name__ == "__main__":
    sys.exit(main())
