from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from variance_copilot.db import sqlalchemy_url, tool_transaction
from variance_copilot.db.ddl import comment_statements

DDL = """
CREATE TABLE md.example (                 -- cf. TABLE1
    code     char(4) PRIMARY KEY,         -- TAB-CODE
    category text    NOT NULL CHECK (category IN (
                 'A','B')),               -- TAB-CAT
    plain    text,
    note     text    DEFAULT '-- not a comment',
    PRIMARY KEY (code)                    -- composite key
);
CREATE TABLE md.other (
    id int  -- it's an id
);
"""


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
        ("postgres://u:p@h/d", "postgresql+psycopg://u:p@h/d"),
        ("postgresql+psycopg://u@h/d", "postgresql+psycopg://u@h/d"),
    ],
)
def test_sqlalchemy_url_uses_psycopg3(url: str, expected: str) -> None:
    assert sqlalchemy_url(url) == expected


def test_comment_statements() -> None:
    assert comment_statements(DDL) == [
        "COMMENT ON TABLE md.example IS 'cf. TABLE1';",
        "COMMENT ON COLUMN md.example.code IS 'TAB-CODE';",
        "COMMENT ON COLUMN md.example.category IS 'TAB-CAT';",
        "COMMENT ON COLUMN md.other.id IS 'it''s an id';",
    ]


@pytest.mark.parametrize(
    ("scope", "error"),
    [
        ([], ValueError),
        (["ES10", "es10"], ValueError),
        (["ES10'; DROP TABLE x; --"], ValueError),
        (["group"], ValueError),
        ("ES10", TypeError),
    ],
)
def test_tool_transaction_rejects_invalid_scope(scope: list[str], error: type[Exception]) -> None:
    engine = create_engine("postgresql+psycopg://nobody@localhost:1/none")

    with pytest.raises(error), tool_transaction(engine, scope):
        pass
