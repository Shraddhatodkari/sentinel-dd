"""Data Loss Prevention: PII / secret detection with checksum validation and redaction.

Detectors use regexes *plus* validators (Luhn for cards, Verhoeff for Aadhaar, ISO-7064 mod-97
for IBAN) to keep false positives low. Redaction strategies:

* ``mask``      -> ``[EMAIL]``
* ``hash``      -> ``[EMAIL:3f2a9c1b]`` (keyed HMAC => stable pseudonym, joinable across docs)
* ``tokenize``  -> reversible pseudonym stored in a :class:`TokenVault` (restorable by privileged roles)
"""

from __future__ import annotations

import hashlib
import hmac
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

Strategy = Literal["mask", "hash", "tokenize"]


def luhn_valid(number: str) -> bool:
    digits = [int(d) for d in re.sub(r"\D", "", number)]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_valid(number: str) -> bool:
    digits = re.sub(r"\D", "", number)
    if len(digits) != 12 or digits[0] in "01":
        return False
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def iban_valid(iban: str) -> bool:
    s = re.sub(r"\s", "", iban).upper()
    if not 15 <= len(s) <= 34:
        return False
    rearranged = s[4:] + s[:4]
    numeric = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(numeric) % 97 == 1


@dataclass(frozen=True)
class Detector:
    label: str
    pattern: re.Pattern[str]
    validator: Callable[[str], bool] | None = None
    severity: Literal["low", "medium", "high", "critical"] = "medium"


DETECTORS: list[Detector] = [
    Detector(
        "PRIVATE_KEY",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
        severity="critical",
    ),
    Detector(
        "API_KEY",
        re.compile(r"\b(?:sk-(?:ant-)?[A-Za-z0-9_\-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36})\b"),
        severity="critical",
    ),
    Detector("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), severity="medium"),
    Detector("CREDIT_CARD", re.compile(r"\b\d(?:[ -]?\d){12,18}\b"), luhn_valid, "critical"),
    Detector("AADHAAR", re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b"), verhoeff_valid, "critical"),
    Detector(
        "IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b"), iban_valid, "high"
    ),
    Detector("US_SSN", re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"), severity="critical"),
    Detector("INDIA_PAN", re.compile(r"\b[A-Z]{3}[PCHABGJLFT][A-Z]\d{4}[A-Z]\b"), severity="high"),
    Detector(
        "PHONE",
        re.compile(
            r"(?<![\w-])(?:\+\d{1,3}(?:[ -]\d{2,5}){2,4}"  # international: +971 50 123 4567
            r"|(?:\+\d{1,3}[ -]?)?(?:\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}"  # NANP: (555) 123-4567
            r"|\d{5}[ -]?\d{5}))(?![\w-])"  # India: 98765 43210
        ),
        severity="medium",
    ),
    Detector(
        "IP_ADDRESS",
        re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
        severity="low",
    ),
]


@dataclass(frozen=True)
class Finding:
    label: str
    start: int
    end: int
    value: str
    severity: str


@dataclass
class RedactionResult:
    text: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def has_pii(self) -> bool:
        return bool(self.findings)

    @property
    def max_severity(self) -> str | None:
        order = ["low", "medium", "high", "critical"]
        if not self.findings:
            return None
        return max((f.severity for f in self.findings), key=order.index)


class TokenVault:
    """Reversible pseudonym store. In production back this with a KMS-encrypted table / HSM."""

    def __init__(self) -> None:
        self._fwd: dict[str, str] = {}
        self._rev: dict[str, str] = {}
        self._lock = threading.Lock()

    def tokenize(self, label: str, value: str) -> str:
        with self._lock:
            if value not in self._fwd:
                token = f"<{label}_{len(self._fwd) + 1:04d}>"
                self._fwd[value] = token
                self._rev[token] = value
            return self._fwd[value]

    def detokenize(self, text: str) -> str:
        with self._lock:
            for token, value in self._rev.items():
                text = text.replace(token, value)
        return text


class DLPEngine:
    def __init__(
        self, secret: str = "dlp-pepper", detectors: list[Detector] | None = None, vault: TokenVault | None = None
    ) -> None:
        self._secret = secret.encode()
        self.detectors = detectors or DETECTORS
        self.vault = vault or TokenVault()

    def scan(self, text: str) -> list[Finding]:
        findings: list[Finding] = []
        taken: list[tuple[int, int]] = []
        for det in self.detectors:  # ordered by priority; earlier detectors win overlaps
            for m in det.pattern.finditer(text):
                s, e = m.span()
                value = m.group(0)
                if det.validator and not det.validator(value):
                    continue
                if any(s < te and e > ts for ts, te in taken):
                    continue
                taken.append((s, e))
                findings.append(Finding(det.label, s, e, value, det.severity))
        return sorted(findings, key=lambda f: f.start)

    def _replacement(self, f: Finding, strategy: Strategy) -> str:
        if strategy == "mask":
            return f"[{f.label}]"
        if strategy == "hash":
            digest = hmac.new(self._secret, f.value.encode(), hashlib.sha256).hexdigest()[:8]
            return f"[{f.label}:{digest}]"
        return self.vault.tokenize(f.label, f.value)

    def redact(self, text: str, strategy: Strategy = "mask") -> RedactionResult:
        findings = self.scan(text)
        out: list[str] = []
        cursor = 0
        for f in findings:
            out.append(text[cursor : f.start])
            out.append(self._replacement(f, strategy))
            cursor = f.end
        out.append(text[cursor:])
        return RedactionResult("".join(out), findings)
