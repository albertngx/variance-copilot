from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from variance_copilot.config import (
    AuthMode,
    ConfigError,
    Environment,
    LLMProvider,
    LogLevel,
    McpTransport,
    ReplayMode,
    Role,
    Settings,
    load_anomalies,
    load_dev_users,
    load_materiality,
    load_model_prices,
)

REPO_CONFIG = Path(__file__).parents[2] / "config"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate tests from the developer's shell environment."""
    import os

    for name in list(os.environ):
        if name.startswith("VC_"):
            monkeypatch.delenv(name)


def make_settings(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[call-arg]


def load_from_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


# --- Settings -------------------------------------------------------------


def test_defaults_match_spec() -> None:
    s = make_settings()

    assert s.env is Environment.DEV
    assert s.database_url is None
    assert s.llm_provider is LLMProvider.OPENAI
    assert s.llm_model is None
    assert s.judge_provider is None
    assert s.judge_model is None
    assert s.embedding_provider is None
    assert s.embedding_model is None
    assert s.embedding_dim == 1024
    assert s.aws_region == "eu-central-1"
    assert s.azure_openai_endpoint is None
    assert s.azure_openai_deployment is None
    assert s.max_turns == 8
    assert s.max_tool_calls == 12
    assert s.max_cost_usd_per_run == Decimal("1.00")
    assert s.store_llm_content is True
    assert s.auth_mode is AuthMode.DEV
    assert s.oidc_issuer is None
    assert s.oidc_audience is None
    assert s.oidc_jwks_url is None
    assert s.dev_jwt_secret is None
    assert s.replay_mode is ReplayMode.OFF
    assert s.otel_exporter_otlp_endpoint is None
    assert s.log_level is LogLevel.INFO
    assert s.mcp_http_enabled is False
    assert s.mcp_transport is McpTransport.MEMORY
    assert s.mcp_server_url is None


def test_reads_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    s = load_from_env(
        monkeypatch,
        VC_ENV="ci",
        VC_LLM_PROVIDER="replay",
        VC_EMBEDDING_DIM="768",
        VC_STORE_LLM_CONTENT="false",
        VC_DATABASE_URL="postgresql://u:hunter2pw@h/db",
    )

    assert s.env is Environment.CI
    assert s.llm_provider is LLMProvider.REPLAY
    assert s.embedding_dim == 768
    assert s.store_llm_content is False
    assert s.database_url is not None
    assert "hunter2pw" not in repr(s)


def test_invalid_enum_value_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match=r"env\n.*'dev', 'ci', 'stg' or 'prod'"):
        load_from_env(monkeypatch, VC_ENV="foo")


@pytest.mark.parametrize(
    ("name", "value"),
    [("VC_EMBEDDING_DIM", "0"), ("VC_MAX_TURNS", "0"), ("VC_MAX_COST_USD_PER_RUN", "-1")],
)
def test_out_of_range_limits_rejected(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        load_from_env(monkeypatch, **{name: value})


def test_dev_auth_rejected_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="VC_AUTH_MODE=dev is not allowed when VC_ENV=prod"):
        load_from_env(monkeypatch, VC_ENV="prod")


def test_prod_with_oidc_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    s = load_from_env(
        monkeypatch,
        VC_ENV="prod",
        VC_AUTH_MODE="oidc",
        VC_OIDC_ISSUER="https://login.example/tenant",
        VC_OIDC_AUDIENCE="variance-copilot",
        VC_OIDC_JWKS_URL="https://login.example/tenant/keys",
    )

    assert s.auth_mode is AuthMode.OIDC


def test_oidc_requires_issuer_audience_and_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="VC_OIDC_AUDIENCE, VC_OIDC_JWKS_URL"):
        load_from_env(monkeypatch, VC_AUTH_MODE="oidc", VC_OIDC_ISSUER="https://x")


def test_http_mcp_transport_requires_server_url(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="VC_MCP_SERVER_URL"):
        load_from_env(monkeypatch, VC_MCP_TRANSPORT="http")


# --- YAML files -----------------------------------------------------------


def test_materiality_matches_spec() -> None:
    m = load_materiality(REPO_CONFIG)

    assert m.currency == "USD"
    assert m.pnl_line.group.absolute == 10_000_000
    assert m.pnl_line.group.relative == Decimal("0.05")
    assert m.pnl_line.group.relative_floor == 2_000_000
    assert m.pnl_line.company_code.absolute == 2_000_000
    assert m.pnl_line.company_code.relative_floor == 1_000_000
    assert m.drilldown.pareto_coverage == Decimal("0.80")
    assert m.drilldown.max_contributors == 5
    assert m.drilldown.min_contributor == 250_000


def test_anomaly_thresholds_match_spec() -> None:
    rules = load_anomalies(REPO_CONFIG).rules

    for rule in (rules.a1, rules.a2, rules.a3, rules.a4):
        assert rule.threshold == 250_000
    assert rules.a5.threshold == 1_000_000
    assert rules.a5.z_score == 3
    assert rules.a5.trailing_months == 12
    assert rules.a6.document_types == ["SA"]
    assert rules.a6.round_multiple == 100_000
    assert rules.a7.threshold == 10_000
    assert rules.a8.deviation_pp == 5
    assert rules.a8.trailing_months == 6


def test_model_prices_load() -> None:
    prices = load_model_prices(REPO_CONFIG)

    assert prices.models["replay"].input_per_mtok == 0


def test_dev_users_match_spec() -> None:
    users = load_dev_users(REPO_CONFIG).users

    assert set(users) == {
        "analyst_us",
        "analyst_es",
        "reviewer_es",
        "group_fpa",
        "auditor",
        "admin",
    }
    assert users["analyst_us"].company_scope == ["US10"]
    assert users["analyst_es"].company_scope == ["ES10", "DE10"]
    assert users["reviewer_es"].roles == [Role.REVIEWER]
    assert users["group_fpa"].company_scope == ["group"]
    assert users["auditor"].company_scope == []


def test_unknown_key_reports_file_and_field(tmp_path: Path) -> None:
    text = (REPO_CONFIG / "materiality.yaml").read_text() + "unexpected: 1\n"
    (tmp_path / "materiality.yaml").write_text(text)

    with pytest.raises(ConfigError, match=r"materiality\.yaml(.|\n)*unexpected"):
        load_materiality(tmp_path)


def test_invalid_company_code_rejected(tmp_path: Path) -> None:
    (tmp_path / "dev_users.yaml").write_text(
        "version: 1\nusers:\n  x:\n    roles: [analyst]\n    company_scope: [us10]\n"
    )

    with pytest.raises(ConfigError, match="company_scope"):
        load_dev_users(tmp_path)


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_model_prices(tmp_path)
