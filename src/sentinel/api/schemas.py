"""API request/response contracts (OpenAPI is generated from these). Inputs are strictly validated."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginRequest(Strict):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: int
    username: str
    display_name: str
    roles: list[str]
    permissions: list[str]
    tenant: str


class CaseCreate(Strict):
    query: str = Field(min_length=2, max_length=300, examples=["Apple Inc."])
    cik: int | None = Field(None, ge=1, le=9_999_999_999)
    lei: str | None = Field(None, pattern=r"^[A-Z0-9]{20}$")
    ticker: str | None = Field(None, pattern=r"^[A-Za-z.\-]{1,10}$")
    question: str = Field("", max_length=1000)


class Decision(Strict):
    approve: bool
    comment: str = Field(min_length=10, max_length=4000)


class ScreenRequest(Strict):
    name: str = Field(min_length=2, max_length=300)
    entity_type: Literal["individual", "entity"] | None = None


class UserCreate(Strict):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=256)
    roles: list[Literal["analyst", "reviewer", "senior_reviewer", "auditor", "admin"]] = Field(min_length=1)
    display_name: str = Field("", max_length=128)


class CaseList(BaseModel):
    total: int
    items: list[dict[str, Any]]
