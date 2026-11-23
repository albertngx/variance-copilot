from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from variance_copilot.agent.ask import ask_as_dev_user, dev_principal, read_data_info
from variance_copilot.agent.providers.replay import canonical_json
from variance_copilot.config import ConfigError, Settings

pytestmark = pytest.mark.anyio

CHECKSUMS = {"fin.journal_line": "ab12", "md.company_code": "cd34"}


def test_data_info_hashes_the_table_checksums_and_reads_as_of(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"as_of": "2026-10-09", "sha256": CHECKSUMS}))

    info = read_data_info(manifest)

    assert info.as_of == "2026-10-09"
    assert info.version == hashlib.sha256(canonical_json(CHECKSUMS)).hexdigest()


def test_a_missing_manifest_asks_for_a_seed(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="run `vc seed` first"):
        read_data_info(tmp_path / "manifest.json")


def test_an_unknown_dev_user_is_refused_before_any_query() -> None:
    engine = create_engine("postgresql+psycopg://nobody@localhost:1/none")

    with pytest.raises(ConfigError, match="unknown development user 'bob'; one of admin, "):
        dev_principal("bob", engine)


async def test_the_http_transport_is_refused_until_the_remote_endpoint_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in [name for name in os.environ if name.startswith("VC_")]:
        monkeypatch.delenv(name)
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, mcp_transport="http", mcp_server_url="http://localhost:8000/mcp"
    )

    with pytest.raises(ConfigError, match="needs the remote MCP endpoint"):
        await ask_as_dev_user("Why?", "analyst_us", settings)
