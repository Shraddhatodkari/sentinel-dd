"""Build supervised fine-tuning (SFT) datasets from reviewed cases.

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


def build_records(
    cases: Iterable[dict[str, Any]], dlp: DLPEngine | None = None, fmt: Literal["chat", "instruction"] = "chat"
) -> list[dict[str, Any]]:
    dlp = dlp or DLPEngine()
    out: list[dict[str, Any]] = []
    for c in cases:
        if c.get("review_status") != "approved" or not c.get("report"):
            continue
        findings = dlp.redact(json.dumps(c.get("findings", []), ensure_ascii=False), "mask").text
        report = dlp.redact(c["report"], "mask").text
        prompt = f"Entity: {c['entity']}\nFindings:\n{findings}\nWrite the due-diligence memo."
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
