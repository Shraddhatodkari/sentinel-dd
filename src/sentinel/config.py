"""Centralised, typed configuration (12-factor: every setting is overridable via environment variables).

All settings use the ``SENTINEL_`` prefix (e.g. ``SENTINEL_LLM_PROVIDER=anthropic``). Secrets are typed as
``SecretStr`` so they never appear in logs or ``repr`` output. ``validate_for_production`` refuses to start a
``prod`` deployment with development defaults.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["anthropic", "openai", "bedrock", "vertex", "extractive"]
EmbeddingBackend = Literal["hashing", "sentence_transformers", "openai"]

DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me-0000000000"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SENTINEL_", env_file=".env", extra="ignore")

    # ------------------------------------------------------------------ runtime
    env: Literal["dev", "test", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    data_dir: Path = Path("var")  # raw source store, checkpoints, model registry (mount a volume in prod)

    # ------------------------------------------------------------------ persistence
    database_url: str = "sqlite:///var/sentinel.db"  # prod: postgresql+psycopg://user:pass@host/db
    checkpoint_url: str = "var/checkpoints.sqlite"  # LangGraph checkpointer (sqlite path or postgres DSN)
    db_echo: bool = False
    migrate_on_start: bool = True  # multi-replica deployments: false + run `sentinel db upgrade` as a Job

    # ------------------------------------------------------------------ LLM providers (primary + fallbacks)
    llm_provider: LLMProvider = "extractive"
    llm_fallbacks: list[LLMProvider] = Field(default_factory=list)
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-4-5"
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    bedrock_model_id: str = "anthropic.claude-3-5-sonnet-20240620-v1:0"
    aws_region: str = "us-east-1"
    vertex_project: str | None = None
    vertex_location: str = "us-central1"
    vertex_model: str = "gemini-1.5-pro"
    llm_max_tokens: int = 1024  # ceiling on output tokens per LLM call
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 3
    # USD per 1M tokens (input, output) used for cost metrics; override to match your contract pricing
    llm_price_per_mtok: dict[str, tuple[float, float]] = Field(
        default_factory=lambda: {"claude-sonnet-4-5": (3.0, 15.0), "gpt-4o-mini": (0.15, 0.6)}
    )

    mlflow_tracking_uri: str | None = None  # optional: mirror model-registry metrics/params to MLflow

    # ------------------------------------------------------------------ external sources
    sec_user_agent: str = "Sentinel-DD compliance-research admin@example.com"  # SEC requires a contact UA
    http_timeout_s: float = 30.0
    http_max_retries: int = 3
    http_backoff_base_s: float = 1.0
    http_rate_limits_enabled: bool = True  # honour published fair-access intervals (SEC 10 req/s, GDELT 1/5 s)
    source_cache_ttl_s: int = 6 * 3600  # per-entity source responses re-used within this window
    sanctions_max_age_h: int = 36  # sanctions-list freshness SLA (h); stale lists force review (TPRM-DAT-03)
    pep_max_age_h: int = 24 * 14  # PEP-list freshness SLA (h)
    enable_gdelt: bool = True
    # GDELT window: in testing, 3-month windows returned an empty {} while a 1-week window returned articles
    gdelt_timespan: str = Field(default="1week", pattern=r"^\d+(min|h|hours|d|days|week|weeks|month|months)$")
    enable_opensanctions_peps: bool = False  # OpenSanctions is CC BY-NC: enable only with a commercial licence

    # ------------------------------------------------------------------ retrieval
    embedding_backend: EmbeddingBackend = "hashing"
    embedding_dim: int = 384
    chunk_size: int = 900
    chunk_overlap: int = 120
    retrieval_top_k: int = 6
    grounding_threshold: float = 0.6

    # ------------------------------------------------------------------ screening
    screening_review_threshold: float = 0.88  # name similarity at/above which a hit is a potential match
    screening_max_related_parties: int = 60

    # ------------------------------------------------------------------ security
    jwt_secret: SecretStr = SecretStr(DEV_JWT_SECRET)
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 30
    jwt_issuer: str = "sentinel-dd"
    rate_limit_per_minute: int = 120
    login_rate_limit_per_minute: int = 10
    cors_origins: list[str] = Field(default_factory=list)  # only needed if the UI is served from another origin
    local_auth_enabled: bool = True  # username/password login against the users table (disable behind an IdP)
    password_min_length: int = 12

    # ------------------------------------------------------------------ governance / execution
    policy_version: str = "tprm-policy-2026.10"
    auto_clear_enabled: bool = False  # conservative default: every case gets a human decision
    max_agent_steps: int = 40
    runner_mode: Literal["background", "inline"] = "background"
    runner_workers: int = 2

    @field_validator("anthropic_api_key", "openai_api_key", "vertex_project", "mlflow_tracking_uri", mode="before")
    @classmethod
    def _blank_is_unset(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("jwt_secret", mode="before")
    @classmethod
    def _blank_secret_is_dev_default(cls, v: object) -> object:
        # An empty value (e.g. copied from .env.example) falls back to the dev secret, which prod refuses to run with.
        return DEV_JWT_SECRET if isinstance(v, str) and not v.strip() else v

    @property
    def model_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def raw_store_dir(self) -> Path:
        return self.data_dir / "raw"

    def validate_for_production(self) -> list[str]:
        problems: list[str] = []
        if self.env != "prod":
            return problems
        if self.jwt_secret.get_secret_value() == DEV_JWT_SECRET or len(self.jwt_secret.get_secret_value()) < 32:
            problems.append("SENTINEL_JWT_SECRET must be set to a strong secret (>=32 chars)")
        if self.database_url.startswith("sqlite"):
            problems.append("SENTINEL_DATABASE_URL must point to PostgreSQL in prod")
        if any(x in self.sec_user_agent for x in ("example.com", "yourorg.com")):
            problems.append("SENTINEL_SEC_USER_AGENT must contain a real contact email (SEC fair-access policy)")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
