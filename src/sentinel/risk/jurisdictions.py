"""Jurisdiction risk policy (configuration, owned by Compliance — not a regulatory determination).

Default tiers and their basis:
* prohibited — FATF "High-Risk Jurisdictions subject to a Call for Action" with countermeasures (DPRK, Iran).
* high — FATF call-for-action (Myanmar) and jurisdictions subject to comprehensive or broad U.S./UK/EU
  sanctions programmes (Russia, Belarus, Syria, Cuba). This is a *deployment policy choice*.

The FATF lists change three times a year: compliance must review this file against the current FATF public
statement before go-live and on every plenary. Override with ``SENTINEL_JURISDICTION_POLICY_PATH`` (JSON).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_POLICY: dict[str, Any] = {
    "version": "jurisdiction-policy-2026.10-default",
    "review_note": "Default tiers; verify against the current FATF public statement and internal risk appetite.",
    "tiers": {
        "prohibited": {"points": 40, "countries": ["KP", "IR"]},
        "high": {"points": 15, "countries": ["MM", "RU", "BY", "SY", "CU"]},
        "elevated": {"points": 6, "countries": []},
    },
}

COUNTRY_ALIASES = {
    "RUSSIA": "RU",
    "RUSSIAN FEDERATION": "RU",
    "IRAN": "IR",
    "NORTH KOREA": "KP",
    "BELARUS": "BY",
    "SYRIA": "SY",
    "CUBA": "CU",
    "MYANMAR": "MM",
    "UNITED STATES": "US",
}


@lru_cache
def load_policy(path: str | None = None) -> dict[str, Any]:
    p = path or os.environ.get("SENTINEL_JURISDICTION_POLICY_PATH")
    if p and Path(p).exists():
        return json.loads(Path(p).read_text())
    return DEFAULT_POLICY


def country_code(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().upper()
    if "-" in v and len(v.split("-")[0]) == 2:  # GLEIF jurisdiction e.g. "US-CA"
        v = v.split("-")[0]
    return COUNTRY_ALIASES.get(v, v if len(v) == 2 else None)


def tier_for(country: str | None, policy: dict[str, Any] | None = None) -> tuple[str, int]:
    policy = policy or load_policy()
    cc = country_code(country)
    for tier in ("prohibited", "high", "elevated"):
        t = policy["tiers"].get(tier, {})
        if cc and cc in t.get("countries", []):
            return tier, int(t.get("points", 0))
    return "standard", 0
