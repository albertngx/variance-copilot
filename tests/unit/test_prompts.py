from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from variance_copilot.agent.prompts import PromptError, load_prompt

REPO_PROMPTS = Path(__file__).parents[2] / "prompts"

META = """\
id: {id}
version: {version}
owner: Test Owner
changelog:
  - version: 1
    date: 2026-11-08
    change: First version."""


def write_prompt(
    directory: Path, body: str, *, name: str = "demo", meta: str | None = None
) -> None:
    text = meta if meta is not None else META.format(id=name, version=1)
    (directory / f"{name}.md").write_text(f"---\n{text}\n---\n\n{body}\n", encoding="utf-8")


# --- Repository prompts ----------------------------------------------------


def test_qa_system_prompt_renders_with_its_sha256() -> None:
    prompt = load_prompt("system/qa", REPO_PROMPTS)

    rendered = prompt.render()

    assert (rendered.prompt_id, rendered.version) == ("qa", prompt.meta.version)
    assert rendered.text.startswith("You are Variance Copilot")
    assert rendered.sha256 == hashlib.sha256(rendered.text.encode()).hexdigest()
    assert rendered.sha256 == prompt.sha256
    assert "---" not in rendered.text
    assert "{{F3-2|abs}}" in rendered.text


def test_repair_prompt_lists_the_violations() -> None:
    prompt = load_prompt("repair", REPO_PROMPTS)
    violations = (
        "- headline: digits typed instead of a placeholder\n- drivers[0]: unknown fact F9-9"
    )

    rendered = prompt.render(violations=violations)

    assert prompt.variables == {"violations"}
    assert violations in rendered.text
    assert rendered.sha256 != prompt.render(violations="- other").sha256


def test_front_matter_carries_owner_and_changelog() -> None:
    for name in ("system/qa", "repair"):
        meta = load_prompt(name, REPO_PROMPTS).meta
        assert meta.owner
        assert meta.version in {entry.version for entry in meta.changelog}


# --- Rendering -------------------------------------------------------------


def test_render_requires_exactly_the_template_variables() -> None:
    repair = load_prompt("repair", REPO_PROMPTS)
    qa = load_prompt("system/qa", REPO_PROMPTS)

    with pytest.raises(PromptError, match=r"missing \['violations'\]"):
        repair.render()
    with pytest.raises(PromptError, match=r"unexpected \['question'\]"):
        qa.render(question="Why?")


def test_dollar_signs_are_escaped_by_doubling(tmp_path: Path) -> None:
    write_prompt(tmp_path, "Amounts in $$ only, for $who.")

    assert (
        load_prompt("demo", tmp_path).render(who="analysts").text
        == "Amounts in $ only, for analysts."
    )


def test_sha256_ignores_surrounding_blank_lines(tmp_path: Path) -> None:
    write_prompt(tmp_path, "Body text.")
    first = load_prompt("demo", tmp_path).sha256
    write_prompt(tmp_path, "\n\nBody text.\n\n")

    assert load_prompt("demo", tmp_path).sha256 == first


# --- Invalid files ---------------------------------------------------------


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(PromptError, match="cannot read prompt"):
        load_prompt("absent", tmp_path)


def test_file_without_front_matter(tmp_path: Path) -> None:
    (tmp_path / "demo.md").write_text("Just a body.\n", encoding="utf-8")

    with pytest.raises(PromptError, match="no front matter"):
        load_prompt("demo", tmp_path)


@pytest.mark.parametrize(
    ("meta", "error"),
    [
        ("id: [unclosed", "invalid front matter"),
        (META.format(id="demo", version=1).replace("owner: Test Owner\n", ""), "owner"),
        (META.format(id="demo", version=2), "changelog has no entry for version 2"),
        (META.format(id="demo", version=1) + "\nreviewer: someone", "Extra inputs"),
        ("id: demo\nversion: 1\nowner: Test Owner\nchangelog: []", "changelog"),
    ],
    ids=["yaml", "missing owner", "unlogged version", "unknown field", "empty changelog"],
)
def test_invalid_front_matter(tmp_path: Path, meta: str, error: str) -> None:
    write_prompt(tmp_path, "Body.", meta=meta)

    with pytest.raises(PromptError, match=error):
        load_prompt("demo", tmp_path)


def test_id_must_match_the_file_name(tmp_path: Path) -> None:
    write_prompt(tmp_path, "Body.", meta=META.format(id="other", version=1))

    with pytest.raises(PromptError, match="declares id 'other'; expected 'demo'"):
        load_prompt("demo", tmp_path)


def test_a_stray_dollar_sign_is_rejected_at_load(tmp_path: Path) -> None:
    write_prompt(tmp_path, "Costs $5 per call.")

    with pytest.raises(PromptError, match="write '\\$\\$'"):
        load_prompt("demo", tmp_path)
