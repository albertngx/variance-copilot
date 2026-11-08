"""Versioned prompt files with front matter (SPEC §12.5)."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Annotated, Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

DEFAULT_PROMPTS_DIR = Path("prompts")

_FRONT_MATTER = re.compile(r"\A---\n(?P<meta>.*?)\n---\n(?P<body>.*)\Z", re.DOTALL)


class PromptError(Exception):
    """A prompt file is missing or invalid, or was rendered with the wrong variables."""


class _Meta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ChangelogEntry(_Meta):
    version: Annotated[int, Field(ge=1)]
    date: dt.date
    change: Annotated[str, Field(min_length=1)]


class PromptMeta(_Meta):
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
    version: Annotated[int, Field(ge=1)]
    owner: Annotated[str, Field(min_length=1)]
    changelog: Annotated[list[ChangelogEntry], Field(min_length=1)]

    @model_validator(mode="after")
    def _current_version_logged(self) -> Self:
        if all(entry.version != self.version for entry in self.changelog):
            raise ValueError(f"changelog has no entry for version {self.version}")
        return self


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class RenderedPrompt:
    prompt_id: str
    version: int
    text: str
    sha256: str


@dataclass(frozen=True)
class Prompt:
    """A prompt template; ``$name`` marks a variable, since ``{{...}}`` is a fact placeholder."""

    meta: PromptMeta
    template: str

    @property
    def sha256(self) -> str:
        return _sha256(self.template)

    @property
    def variables(self) -> frozenset[str]:
        return frozenset(Template(self.template).get_identifiers())

    def render(self, **variables: str) -> RenderedPrompt:
        missing, unexpected = self.variables - variables.keys(), variables.keys() - self.variables
        if missing or unexpected:
            raise PromptError(
                f"prompt {self.meta.id} needs {sorted(self.variables)}; "
                f"missing {sorted(missing)}, unexpected {sorted(unexpected)}"
            )
        text = Template(self.template).substitute(variables)
        return RenderedPrompt(self.meta.id, self.meta.version, text, _sha256(text))


def load_prompt(name: str, prompts_dir: Path = DEFAULT_PROMPTS_DIR) -> Prompt:
    """Load ``<prompts_dir>/<name>.md``, e.g. ``system/qa``."""
    path = prompts_dir / f"{name}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptError(f"cannot read prompt {path}: {exc}") from exc
    match = _FRONT_MATTER.match(text)
    if match is None:
        raise PromptError(f"prompt {path} has no front matter between '---' lines")
    try:
        raw: Any = yaml.safe_load(match["meta"])
        meta = PromptMeta.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as exc:
        raise PromptError(f"invalid front matter in {path}:\n{exc}") from exc
    if meta.id != path.stem:
        raise PromptError(f"prompt {path} declares id {meta.id!r}; expected {path.stem!r}")
    template = match["body"].strip()
    if not Template(template).is_valid():
        raise PromptError(f"prompt {path} has a '$' that is not a variable; write '$$'")
    return Prompt(meta, template)
