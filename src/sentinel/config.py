"""Centralised, typed configuration (12-factor: everything overridable via env vars).

All settings are prefixed with ``SENTINEL_`` (e.g. ``SENTINEL_LLM_PROVIDER=anthropic``).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["anthropic", "openai", "bedrock", "vertex", "fake"]
VectorBackend = Literal["memory", "qdrant", "pgvector"]
EmbeddingBackend = Literal["hashing", "sentence_transformers", "openai"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SENTINEL_", env_file=".env", extra="ignore")

    # --- runtime ---
    env: Literal["dev", "test", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    service_name: str = "sentinel-dd"

    # --- LLM providers (primary + ordered fallbacks) ---
    llm_provider: LLMProvider = "fake"
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
    llm_max_tokens: int = 1024
    llm_temperature: float = 0.0
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 3

    # --- retrieval ---
    embedding_backend: EmbeddingBackend = "hashing"
    embedding_dim: int = 384
    vector_backend: VectorBackend = "memory"
    qdrant_url: str = "http://localhost:6333"
    postgres_dsn: str = "postgresql://sentinel:sentinel@localhost:5432/sentinel"
    chunk_size: int = 800
    chunk_overlap: int = 120
    retrieval_top_k: int = 6
    grounding_threshold: float = 0.35

    # --- security ---
    jwt_secret: SecretStr = SecretStr("change-me-in-prod-please-32-bytes-min!!")
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 60
    rate_limit_per_minute: int = 120
    audit_log_path: str = "var/audit.log"

    # --- governance ---
    hitl_risk_threshold: float = 0.6  # risk >= threshold requires human approval
    max_agent_steps: int = 25

    # --- ML ---
    model_dir: str = "var/models"
    mlflow_tracking_uri: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
