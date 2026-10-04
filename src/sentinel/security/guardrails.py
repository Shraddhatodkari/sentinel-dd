"""Input / context / output guardrails.

* **Input**: prompt-injection & jailbreak heuristics, length limits, scope (topic) control.
* **Context**: neutralises *indirect* prompt injection hidden inside retrieved documents.
* **Output**: PII leakage, mandatory citations (grounding), and prohibited-claim checks.

Heuristic scoring is deliberately transparent and auditable; an ML/LLM classifier can be plugged
in via ``extra_checks`` without changing callers.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from sentinel.security.dlp import DLPEngine

INJECTION_PATTERNS: list[tuple[re.Pattern[str], float, str]] = [
    (
        re.compile(r"ignore (all |any )?(the )?(previous|prior|above) (instructions|prompts?|rules)", re.I),
        0.9,
        "instruction_override",
    ),
    (
        re.compile(r"disregard (your|the|all) (rules|instructions|guidelines|system prompt)", re.I),
        0.9,
        "instruction_override",
    ),
    (
        re.compile(
            r"(reveal|print|show|repeat|leak).{0,30}(system prompt|hidden instructions|your instructions)", re.I
        ),
        0.8,
        "prompt_exfiltration",
    ),
    (
        re.compile(r"\byou are now\b|\bact as (?:an? )?(?:unfiltered|jailbroken|DAN)\b|\bDAN mode\b", re.I),
        0.7,
        "role_hijack",
    ),
    (re.compile(r"(developer|god|sudo|admin) mode", re.I), 0.6, "privilege_escalation"),
    (re.compile(r"</?(system|assistant|instructions?)>", re.I), 0.6, "tag_spoofing"),
    (
        re.compile(r"(exfiltrate|send|post|upload).{0,40}(https?://|webhook|api key|credentials)", re.I),
        0.8,
        "data_exfiltration",
    ),
    (re.compile(r"approve (this|the) (vendor|case) (automatically|without review)", re.I), 0.7, "governance_bypass"),
    (
        re.compile(
            r"(skip|bypass|disable|turn off) (the |all )?(sanctions?|compliance|kyc|aml|policy|guardrails?)"
            r"( check| screening| review| controls?)?",
            re.I,
        ),
        0.8,
        "governance_bypass",
    ),
]

PROHIBITED_OUTPUT = [
    (re.compile(r"\b(guaranteed|100% safe|no risk whatsoever)\b", re.I), "overclaiming"),
    (re.compile(r"\bthis constitutes legal advice\b", re.I), "legal_advice"),
]

_B64 = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")
_CITATION = re.compile(r"\[[^\]]+#\d+\]|\[[A-Za-z0-9_\-./]+:\d+\]")


@dataclass
class GuardrailResult:
    allowed: bool
    score: float = 0.0
    violations: list[str] = field(default_factory=list)
    sanitized: str | None = None


class Guardrails:
    def __init__(
        self,
        dlp: DLPEngine | None = None,
        block_threshold: float = 0.7,
        max_input_chars: int = 8000,
        allowed_topics: tuple[str, ...] | None = None,
        extra_checks: list[Callable[[str], tuple[float, str] | None]] | None = None,
    ) -> None:
        self.dlp = dlp or DLPEngine()
        self.block_threshold = block_threshold
        self.max_input_chars = max_input_chars
        self.allowed_topics = allowed_topics
        self.extra_checks = extra_checks or []

    # ---------------- input ----------------
    def injection_score(self, text: str) -> tuple[float, list[str]]:
        score = 0.0
        hits: list[str] = []
        candidates = [text]
        for blob in _B64.findall(text):  # catch base64-smuggled instructions
            try:
                candidates.append(base64.b64decode(blob, validate=True).decode("utf-8", "ignore"))
            except Exception:  # noqa: S112 - not valid base64, ignore
                continue
        for cand in candidates:
            for pat, weight, name in INJECTION_PATTERNS:
                if pat.search(cand):
                    score = max(score, weight) + (0.05 if name not in hits and hits else 0)
                    if name not in hits:
                        hits.append(name)
        for check in self.extra_checks:
            res = check(text)
            if res:
                score = max(score, res[0])
                hits.append(res[1])
        return min(score, 1.0), hits

    def check_input(self, text: str) -> GuardrailResult:
        violations: list[str] = []
        if len(text) > self.max_input_chars:
            return GuardrailResult(False, 1.0, ["input_too_long"])
        score, hits = self.injection_score(text)
        violations += [f"injection:{h}" for h in hits]
        if self.allowed_topics and not any(t in text.lower() for t in self.allowed_topics):
            violations.append("off_topic")
            score = max(score, self.block_threshold)
        redacted = self.dlp.redact(text, "mask")
        if redacted.has_pii:
            violations.append("pii_in_input_redacted")
        return GuardrailResult(score < self.block_threshold, score, violations, redacted.text)

    # ---------------- context (indirect injection) ----------------
    def sanitize_context(self, chunk: str) -> tuple[str, bool]:
        """Neutralise instructions embedded in retrieved content. Returns (text, was_flagged)."""
        flagged = False
        out = chunk
        for pat, _w, _n in INJECTION_PATTERNS:
            if pat.search(out):
                flagged = True
                out = pat.sub("[REMOVED: embedded instruction]", out)
        return out, flagged

    # ---------------- output ----------------
    def check_output(self, text: str, require_citations: bool = True) -> GuardrailResult:
        violations: list[str] = []
        redacted = self.dlp.redact(text, "mask")
        if redacted.has_pii:
            violations.append("pii_leak_redacted")
        for pat, name in PROHIBITED_OUTPUT:
            if pat.search(text):
                violations.append(f"prohibited:{name}")
        is_abstain = text.strip().startswith("INSUFFICIENT_EVIDENCE")
        if require_citations and not is_abstain and not _CITATION.search(text):
            violations.append("missing_citations")
        blocking = [v for v in violations if v.startswith("prohibited") or v == "missing_citations"]
        return GuardrailResult(not blocking, 1.0 if blocking else 0.0, violations, redacted.text)
