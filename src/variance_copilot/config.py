"""Application settings (SPEC §24) and typed loaders for ``config/*.yaml``.

Environment variables use the ``VC_`` prefix and may also come from a local ``.env``.
Invalid values fail fast at construction time with a pydantic ``ValidationError``.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_CONFIG_DIR = Path("config")


class ConfigError(Exception):
    """A configuration file is missing, unreadable or invalid."""


# --- Environment settings ------------------------------------------------


class Environment(StrEnum):
    DEV = "dev"
    CI = "ci"
    STG = "stg"
    PROD = "prod"


class LLMProvider(StrEnum):
    BEDROCK = "bedrock"
    AZURE_OPENAI = "azure_openai"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    REPLAY = "replay"


class AuthMode(StrEnum):
    DEV = "dev"
    OIDC = "oidc"


class ReplayMode(StrEnum):
    OFF = "off"
    RECORD = "record"
    REPLAY = "replay"


class McpTransport(StrEnum):
    MEMORY = "memory"
    HTTP = "http"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Settings(BaseSettings):
    """Process-wide settings, one field per ``VC_*`` variable in SPEC §24."""

    model_config = SettingsConfigDict(
        env_prefix="VC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    env: Environment = Environment.DEV
    database_url: SecretStr | None = None

    llm_provider: LLMProvider = LLMProvider.OPENAI
    llm_model: str | None = None
    judge_provider: LLMProvider | None = None
    judge_model: str | None = None
    embedding_provider: LLMProvider | None = None
    embedding_model: str | None = None
    embedding_dim: Annotated[int, Field(gt=0)] = 1024

    aws_region: str = "eu-central-1"
    azure_openai_endpoint: str | None = None
    azure_openai_deployment: str | None = None

    # Q&A limits from SPEC §12.3; commentary limits are applied by the pipeline.
    max_turns: Annotated[int, Field(ge=1)] = 8
    max_tool_calls: Annotated[int, Field(ge=0)] = 12
    max_cost_usd_per_run: Annotated[Decimal, Field(gt=0)] = Decimal("1.00")

    store_llm_content: bool = True

    auth_mode: AuthMode = AuthMode.DEV
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    dev_jwt_secret: SecretStr | None = None

    replay_mode: ReplayMode = ReplayMode.OFF
    otel_exporter_otlp_endpoint: str | None = None
    log_level: LogLevel = LogLevel.INFO

    mcp_http_enabled: bool = False
    mcp_transport: McpTransport = McpTransport.MEMORY
    mcp_server_url: str | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        # SPEC §16.2: development tokens are disabled in production.
        if self.env is Environment.PROD and self.auth_mode is AuthMode.DEV:
            raise ValueError("VC_AUTH_MODE=dev is not allowed when VC_ENV=prod; use oidc")
        if self.auth_mode is AuthMode.OIDC:
            missing = [
                name
                for name, value in (
                    ("VC_OIDC_ISSUER", self.oidc_issuer),
                    ("VC_OIDC_AUDIENCE", self.oidc_audience),
                    ("VC_OIDC_JWKS_URL", self.oidc_jwks_url),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"VC_AUTH_MODE=oidc requires {', '.join(missing)}")
        if self.mcp_transport is McpTransport.HTTP and not self.mcp_server_url:
            raise ValueError("VC_MCP_TRANSPORT=http requires VC_MCP_SERVER_URL")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached process settings."""
    return Settings()


# --- YAML configuration files --------------------------------------------


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Amount = Annotated[Decimal, Field(ge=0)]
Ratio = Annotated[Decimal, Field(gt=0, le=1)]


class MaterialityThreshold(_ConfigModel):
    absolute: Amount
    relative: Ratio
    relative_floor: Amount


class PnlLineMateriality(_ConfigModel):
    group: MaterialityThreshold
    company_code: MaterialityThreshold


class DrilldownConfig(_ConfigModel):
    pareto_coverage: Ratio
    max_contributors: Annotated[int, Field(ge=1)]
    min_contributor: Amount


class MaterialityConfig(_ConfigModel):
    """``config/materiality.yaml`` (SPEC §9.7)."""

    version: int
    currency: str
    pnl_line: PnlLineMateriality
    drilldown: DrilldownConfig


class AnomalyRule(_ConfigModel):
    enabled: bool = True
    threshold: Amount


class SpikeRule(AnomalyRule):
    z_score: Annotated[Decimal, Field(gt=0)]
    trailing_months: Annotated[int, Field(ge=2)]


class ManualEntryRule(AnomalyRule):
    document_types: list[str]
    round_multiple: Annotated[Decimal, Field(gt=0)]
    days_before_close: Annotated[int, Field(ge=1)]


class GtnRateRule(AnomalyRule):
    deviation_pp: Annotated[Decimal, Field(gt=0)]
    trailing_months: Annotated[int, Field(ge=1)]


class AnomalyRules(_ConfigModel):
    a1: AnomalyRule = Field(alias="A1")
    a2: AnomalyRule = Field(alias="A2")
    a3: AnomalyRule = Field(alias="A3")
    a4: AnomalyRule = Field(alias="A4")
    a5: SpikeRule = Field(alias="A5")
    a6: ManualEntryRule = Field(alias="A6")
    a7: AnomalyRule = Field(alias="A7")
    a8: GtnRateRule = Field(alias="A8")


class AnomalyConfig(_ConfigModel):
    """``config/anomalies.yaml`` (SPEC §9.9). Thresholds are in ``currency``."""

    version: int
    currency: str
    rules: AnomalyRules


class ModelPrice(_ConfigModel):
    input_per_mtok: Amount
    output_per_mtok: Amount


class ModelPrices(_ConfigModel):
    """``config/model_prices.yaml`` (SPEC §15.5): prices per million tokens by model ID."""

    version: int
    currency: str
    models: dict[str, ModelPrice]


class Role(StrEnum):
    """Roles from SPEC §3."""

    ANALYST = "analyst"
    REVIEWER = "reviewer"
    GROUP_ANALYST = "group_analyst"
    AUDITOR = "auditor"
    ADMIN = "admin"
    EVAL = "eval"


GROUP_SCOPE = "group"
CompanyScopeEntry = Annotated[str, Field(pattern=r"^([A-Z]{2}\d{2}|group)$")]


class DevUser(_ConfigModel):
    roles: Annotated[list[Role], Field(min_length=1)]
    company_scope: list[CompanyScopeEntry] = []


class DevUsers(_ConfigModel):
    """``config/dev_users.yaml`` (SPEC §25): local development identities."""

    version: int
    users: dict[str, DevUser]


def _load_yaml[M: BaseModel](path: Path, model: type[M]) -> M:
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration in {path}:\n{exc}") from exc


def load_materiality(config_dir: Path = DEFAULT_CONFIG_DIR) -> MaterialityConfig:
    return _load_yaml(config_dir / "materiality.yaml", MaterialityConfig)


def load_anomalies(config_dir: Path = DEFAULT_CONFIG_DIR) -> AnomalyConfig:
    return _load_yaml(config_dir / "anomalies.yaml", AnomalyConfig)


def load_model_prices(config_dir: Path = DEFAULT_CONFIG_DIR) -> ModelPrices:
    return _load_yaml(config_dir / "model_prices.yaml", ModelPrices)


def load_dev_users(config_dir: Path = DEFAULT_CONFIG_DIR) -> DevUsers:
    return _load_yaml(config_dir / "dev_users.yaml", DevUsers)
