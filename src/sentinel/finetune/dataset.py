"""Build supervised fine-tuning (SFT) datasets from human-reviewed cases (research module, not in the decision path).

Only *human-approved* case outputs become training targets (closing the HITL feedback loop),
and every record is DLP-redacted before export so no PII lands in a training corpus.
Outputs:
* ``chat`` format — messages[] JSONL (OpenAI / Bedrock / Vertex tuning APIs)
* ``instruction`` format — {instruction, input, output} JSONL (HF TRL / PEFT SFTTrainer)
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from sentinel.prompts import REPORT_SYSTEM
from sentinel.security.dlp import DLPEngine


def findings_prompt(case: dict[str, Any], max_signals: int = 40) -> str:
    """Render the structured, evidence-backed layers of a case (as returned by ``CaseService.get``) as the model
    input. The target is the memo the human reviewer approved; the input never contains the reviewer's decision."""
    name = case.get("resolved_name") or case.get("entity_query", "")
    a, p = case.get("assessment") or {}, case.get("policy") or {}
    lines = [f"Entity: {name}"]
    if a:
        lines.append(f"ASSESSMENT {a.get('scorecard_version')}: band {a.get('band')}, score {a.get('score')}")
        lines += [
            f"- factor {f['id']}: value={f['value']} points={f['points']}/{f['cap']}"
            for f in a.get("factors", [])
            if f.get("points")
        ]
    if p:
        lines.append(f"POLICY {p.get('policy_version')}: {p.get('outcome')}")
        lines += [f"- {r['rule_id']}: {r['description']}" for r in p.get("rules", [])]
    lines.append("SIGNALS:")
    lines += [
        f"- {s['signal_type']} ({s['severity']}): {s['label']} [{(s.get('evidence_ids') or [''])[0][:12]}#0]"
        for s in case.get("signals", [])[:max_signals]
    ]
    return "\n".join(lines) + "\nWrite the due-diligence memo."


def build_records(
    cases: Iterable[dict[str, Any]], dlp: DLPEngine | None = None, fmt: Literal["chat", "instruction"] = "chat"
) -> list[dict[str, Any]]:
    """``cases``: case detail payloads (``CaseService.get``). Only cases whose review was **approved** by a human
    become training examples; inputs and targets are DLP-masked."""
    dlp = dlp or DLPEngine()
    out: list[dict[str, Any]] = []
    for c in cases:
        if (c.get("review") or {}).get("status") != "approved" or not c.get("report"):
            continue
        prompt = dlp.redact(findings_prompt(c), "mask").text
        report = dlp.redact(c["report"], "mask").text
        if fmt == "chat":
            out.append(
                {
                    "messages": [
                        {"role": "system", "content": REPORT_SYSTEM},
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": report},
                    ]
                }
            )
        else:
            out.append({"instruction": REPORT_SYSTEM, "input": prompt, "output": report})
    return out


def approved_cases(service: Any, principal: Any, limit: int = 200) -> list[dict[str, Any]]:
    """Load approved cases visible to ``principal`` (e.g. an auditor) from the case store."""
    rows = service.list_cases(principal, status="approved", limit=limit)["items"]
    return [service.get(principal, r["case_id"]) for r in rows]


def split_and_write(
    records: list[dict[str, Any]], out_dir: str | Path, val_frac: float = 0.1, seed: int = 7
) -> dict[str, int]:
    rnd = random.Random(seed)
    recs = list(records)
    rnd.shuffle(recs)
    n_val = max(1, int(len(recs) * val_frac)) if len(recs) > 1 else 0
    splits = {"val": recs[:n_val], "train": recs[n_val:]}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in splits.items():
        with (out / f"{name}.jsonl").open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {k: len(v) for k, v in splits.items()}
