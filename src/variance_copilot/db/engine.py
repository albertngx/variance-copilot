"""SQLAlchemy engine for the configured database (SPEC §22: SQLAlchemy 2 + psycopg 3)."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from variance_copilot.config import ConfigError, get_settings

_PSYCOPG_SCHEME = "postgresql+psycopg://"
_BARE_SCHEMES = ("postgresql://", "postgres://")


def sqlalchemy_url(url: str) -> str:
    """Return ``url`` with the psycopg 3 driver, whatever PostgreSQL scheme it uses."""
    for scheme in _BARE_SCHEMES:
        if url.startswith(scheme):
            return _PSYCOPG_SCHEME + url.removeprefix(scheme)
    return url


def database_url() -> str:
    """The configured ``VC_DATABASE_URL`` with the psycopg 3 driver."""
    secret = get_settings().database_url
    if secret is None:
        raise ConfigError("VC_DATABASE_URL is not set")
    return sqlalchemy_url(secret.get_secret_value())


def make_engine(url: str) -> Engine:
    return create_engine(sqlalchemy_url(url), pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the cached process engine for ``VC_DATABASE_URL``."""
    return make_engine(database_url())
