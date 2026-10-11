"""PostgreSQL + pgvector for integration tests.

Uses ``VC_TEST_DATABASE_URL`` when set (CI service container); otherwise starts a
Testcontainers instance of the same image once per session.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest

PGVECTOR_IMAGE = "pgvector/pgvector:0.8.7-pg16"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = os.environ.get("VC_TEST_DATABASE_URL")
    if url:
        yield url
        return

    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer(PGVECTOR_IMAGE, driver=None) as container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def _vector_extension(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")


@pytest.fixture
def db(database_url: str, _vector_extension: None) -> Iterator[psycopg.Connection]:
    """A connection whose transaction is rolled back after the test."""
    with psycopg.connect(database_url) as conn:
        yield conn
        conn.rollback()
