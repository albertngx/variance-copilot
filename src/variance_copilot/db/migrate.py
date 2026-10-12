"""Alembic entry points used by ``vc db migrate`` and the tests."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

DEFAULT_MIGRATIONS_DIR = Path("migrations")


def alembic_config(url: str, migrations_dir: Path = DEFAULT_MIGRATIONS_DIR) -> Config:
    config = Config()
    config.set_main_option("script_location", str(migrations_dir))
    config.attributes["database_url"] = url
    return config


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str, revision: str = "base") -> None:
    command.downgrade(alembic_config(url), revision)
