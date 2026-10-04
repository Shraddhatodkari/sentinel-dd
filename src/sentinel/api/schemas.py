"""API request/response contracts (OpenAPI is generated from these)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TokenRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    roles: list[str]


class CaseCreate(BaseModel):
    entity: str = Field(min_length=2, max_length=200, examples=["Helios Trade FZE"])
    question: str = Field("", max_length=2000)


class CaseSummary(BaseModel):
    case_id: str
    entity: str | None
    status: str | None
    risk_band: str | None = None


class CaseDetail(BaseModel):
    case_id: str
    entity: str
    status: str
    awaiting_human: bool
    approval_id: str | None = None
    risk: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)
    screening: list[dict[str, Any]] = Field(default_factory=list)
    graph_findings: dict[str, Any] = Field(default_factory=dict)
    report: str = ""
    report_grounding: float = 0.0
    trace: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    approve: bool
    comment: str | None = Field(None, max_length=2000)


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    entity: str | None = None
    k: int = Field(5, ge=1, le=20)


class SearchResponse(BaseModel):
    answer: str
    citations: list[str]
    grounding_score: float
    flagged_injection: bool


class IngestRequest(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_\-]{2,64}$")
    title: str
    text: str = Field(min_length=1, max_length=2_000_000)
    metadata: dict[str, Any] = Field(default_factory=dict)
