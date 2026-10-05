"""Regenerate the settings reference table in docs/configuration.md from ``sentinel.config.Settings``.

python scripts/gen_config_docs.py          # rewrite the table in place
python scripts/gen_config_docs.py --check  # exit 1 if the docs are out of date (used by tests/CI)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from sentinel.config import Settings

DOC = Path(__file__).resolve().parents[1] / "docs" / "configuration.md"
BEGIN, END = "<!-- settings:begin -->", "<!-- settings:end -->"
NOTES = {
    "env": "`prod` enables start-up validation (strong JWT secret, PostgreSQL, real SEC contact)",
    "llm_provider": "`extractive` = no external model; `anthropic` / `openai` / `bedrock` / `vertex`",
    "llm_fallbacks": "JSON list of providers tried in order if the primary fails; extractive is always last",
    "anthropic_api_key": "secret",
    "openai_api_key": "secret",
    "embedding_backend": "`hashing` (offline), `sentence_transformers` (extra `embeddings`), `openai`",
    "grounding_threshold": "minimum citation grounding of the report; below → TPRM-AI-01",
    "jwt_secret": "secret, ≥ 32 chars; blank falls back to a dev value that prod refuses",
    "runner_mode": "`background` thread pool or `inline` (CLI/tests)",
    "runner_workers": "background worker threads per process",
    "llm_price_per_mtok": "USD per 1M tokens (input, output) for cost metrics",
    "max_agent_steps": "LangGraph recursion limit per case",
    "rate_limit_per_minute": "per token/client, per process",
    "login_rate_limit_per_minute": "per client+username, per process",
    "screening_max_related_parties": "cap on parents/subsidiaries/insiders screened per case",
    "password_min_length": "user password policy",
    "policy_version": "recorded on every policy decision",
    "gdelt_timespan": "GDELT DOC API window; in testing 3-month windows returned `{}` while 1 week returned articles",
    "scorecard_version": "recorded on every assessment",
}


def table() -> str:
    src = (Path(__file__).resolve().parents[1] / "src" / "sentinel" / "config.py").read_text()
    comments = {
        m.group(1): m.group(2)
        for m in (re.match(r"\s+(\w+): [^=]+=.*?#\s*(.*)$", line) for line in src.splitlines())
        if m
    }
    rows = ["| Variable | Default | Notes |", "|---|---|---|"]
    for name, f in Settings.model_fields.items():
        d = f.get_default(call_default_factory=True)
        if name == "jwt_secret":
            d = "(dev value)"
        elif hasattr(d, "get_secret_value"):
            d = "—"
        d = "—" if d is None else d
        rows.append(f"| `SENTINEL_{name.upper()}` | `{d}` | {NOTES.get(name) or comments.get(name, '')} |")
    return "\n".join(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    doc = DOC.read_text()
    new = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), f"{BEGIN}\n{table()}\n{END}", doc, flags=re.S)
    if args.check:
        if new != doc:
            print("docs/configuration.md is out of date: run python scripts/gen_config_docs.py", file=sys.stderr)
            return 1
        return 0
    DOC.write_text(new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
