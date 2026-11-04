from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from variance_copilot.tools import catalog
from variance_copilot.tools.catalog import DATA_TOOLS
from variance_copilot.tools.results import ToolResult

TOOLS_PACKAGE = Path(catalog.__file__).parent
FREE_TEXT = {("journal_lines", "text"), ("list_dimension_members", "filter")}
TRANSPORTS = ("mcp", "fastapi", "starlette", "httpx", "uvicorn")


def _resolve(schema: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in node:
        return _resolve(schema, schema["$defs"][node["$ref"].rsplit("/", 1)[-1]])
    return node


def _string_fields(
    schema: dict[str, Any], node: dict[str, Any], name: str = ""
) -> Iterator[tuple[str, dict[str, Any]]]:
    node = _resolve(schema, node)
    options = node.get("anyOf", []) + node.get("allOf", [])
    decimal = any(o.get("type") == "number" for o in options)
    for option in options:
        if not (decimal and option.get("type") == "string"):
            yield from _string_fields(schema, option, name)
    if node.get("type") == "string":
        yield name, node
    if "items" in node:
        yield from _string_fields(schema, node["items"], name)
    for key, child in node.get("properties", {}).items():
        yield from _string_fields(schema, child, key)


def test_catalogue_is_the_six_data_tools() -> None:
    assert [t.name for t in DATA_TOOLS] == [
        "compare",
        "get_fx_rates",
        "get_period_status",
        "get_pnl",
        "journal_lines",
        "list_dimension_members",
    ]


@pytest.mark.parametrize("tool", list(DATA_TOOLS), ids=lambda t: t.name)
def test_no_free_form_sql_inputs(tool: Any) -> None:
    """Every string input is an enum or a pattern, except bounded search text."""
    schema = tool.input_model.model_json_schema()

    for name, field in _string_fields(schema, schema):
        constrained = "enum" in field or "const" in field or "pattern" in field
        if (tool.name, name) in FREE_TEXT:
            assert field.get("maxLength", 0) <= 100
        else:
            assert constrained, f"{tool.name}.{name} accepts free text"


@pytest.mark.parametrize("tool", list(DATA_TOOLS), ids=lambda t: t.name)
def test_inputs_are_strict_and_results_share_the_common_fields(tool: Any) -> None:
    schema = tool.input_model.model_json_schema()

    assert schema.get("additionalProperties") is False
    assert issubclass(tool.output_model, ToolResult)
    assert {"tool_call_id", "warnings", "facts"} <= set(tool.output_model.model_fields)
    assert len(tool.description) > 40


@pytest.mark.parametrize("tool", list(DATA_TOOLS), ids=lambda t: t.name)
def test_no_input_names_a_company_scope_or_run(tool: Any) -> None:
    fields = set(tool.input_model.model_json_schema().get("properties", {}))

    assert not fields & {"company_scope", "roles", "run_id", "principal", "tool_call_id"}


def test_tools_package_imports_no_transport() -> None:
    for path in TOOLS_PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                assert module.split(".")[0] not in TRANSPORTS, f"{path.name} imports {module}"
