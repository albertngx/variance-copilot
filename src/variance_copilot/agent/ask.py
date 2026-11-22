"""Ask a question as a development user (SPEC §17 ``vc ask``, §25)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine, text

from variance_copilot.agent.events import MemoryEvents
from variance_copilot.agent.loop import Agent, DataInfo, Limits, RunResult
from variance_copilot.agent.providers.factory import make_llm_provider
from variance_copilot.agent.providers.replay import canonical_json
from variance_copilot.agent.runs import PostgresRunStore, model_price
from variance_copilot.config import (
    ConfigError,
    McpTransport,
    Settings,
    get_settings,
    load_dev_users,
    load_materiality,
    load_model_prices,
)
from variance_copilot.db.engine import get_engine
from variance_copilot.db.session import app_transaction, tool_transaction
from variance_copilot.generator.seed import DEFAULT_OUTPUT
from variance_copilot.mcp.client import open_tool_client
from variance_copilot.tools.catalog import DATA_TOOLS
from variance_copilot.tools.context import Principal
from variance_copilot.tools.session import ToolSession

MANIFEST = DEFAULT_OUTPUT / "manifest.json"


def read_data_info(manifest: Path = MANIFEST) -> DataInfo:
    """Data version (hash of the seeded tables' checksums) and as-of date from the manifest."""
    try:
        recorded = json.loads(manifest.read_text(encoding="utf-8"))
        checksums, as_of = recorded["sha256"], recorded["as_of"]
    except (OSError, ValueError, KeyError) as exc:
        raise ConfigError(f"cannot read {manifest}; run `vc seed` first ({exc})") from exc
    return DataInfo(hashlib.sha256(canonical_json(checksums)).hexdigest(), as_of)


def dev_principal(user: str, engine: Engine) -> Principal:
    """The principal for a ``config/dev_users.yaml`` user; scope never comes from the question."""
    users = load_dev_users().users
    dev = users.get(user)
    if dev is None:
        raise ConfigError(f"unknown development user {user!r}; one of {', '.join(sorted(users))}")
    with engine.connect() as conn:
        codes = conn.execute(text("SELECT company_code FROM md.company_code")).scalars().all()
    principal = Principal.from_claims(user, dev.roles, dev.company_scope, codes)
    if not principal.has_finance_role or not principal.company_scope:
        raise ConfigError(f"user {user!r} has no finance role or company scope to ask questions")
    return principal


async def ask_as_dev_user(question: str, user: str, settings: Settings | None = None) -> RunResult:
    """Run one question through the agent loop with an in-memory MCP session."""
    settings = settings or get_settings()
    if settings.mcp_transport is McpTransport.HTTP:
        raise ConfigError("VC_MCP_TRANSPORT=http needs the remote MCP endpoint (M2-18); use memory")
    engine = get_engine()
    principal = dev_principal(user, engine)
    provider = make_llm_provider(settings)
    model_id = settings.llm_model or ""
    agent = Agent(
        provider=provider,
        model_id=model_id,
        price=model_price(load_model_prices(), model_id),
        store=PostgresRunStore(lambda: app_transaction(engine)),
        events=MemoryEvents(),
        data=read_data_info(),
        limits=Limits.from_settings(settings),
    )
    run_id = uuid4()
    session = ToolSession(
        DATA_TOOLS,
        principal,
        lambda scope: tool_transaction(engine, scope),
        load_materiality(),
        run_id=run_id,
    )
    async with open_tool_client(session) as client:
        return await agent.ask(question, principal, client, session.ledger, run_id=run_id)
