"""Adverse-media taxonomy classifier (versioned, rule-based, explainable).

Each headline/summary is matched against category lexicons. The matched terms are returned so a reviewer can
see exactly why an item was flagged. A mention is classified only if it is *relevant* (it names the subject).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TAXONOMY_VERSION = "media-taxonomy-2026.10"

CATEGORIES: dict[str, tuple[str, list[str]]] = {
    "financial_crime": (
        "high",
        [
            r"fraud\w*",
            r"money[- ]launder\w*",
            r"brib\w*",
            r"corrupt\w*",
            r"embezzl\w*",
            r"kickbacks?",
            r"fcpa",
            r"ponzi",
            r"insider trading",
            r"market manipulation",
            r"wire fraud",
            r"tax evasion",
            r"false claims act",
        ],
    ),
    "sanctions_export_controls": (
        "high",
        [
            r"sanctions? (?:violation|evasion|breach)\w*",
            r"violat\w* (?:u\.s\. )?sanctions",
            r"export controls?",
            r"embargo",
            r"\bofac\b",
            r"\bitar\b",
            r"designated (?:by|under)",
            r"blocked persons?",
            r"evad\w* sanctions",
        ],
    ),
    "enforcement_action": (
        "high",
        [
            r"indict\w*",
            r"plead\w* guilty",
            r"pleads? guilty",
            r"convicted",
            r"sentenced",
            r"charged with",
            r"criminal (?:charges?|case|investigation)",
            r"deferred prosecution",
            r"non-prosecution agreement",
            r"consent (?:order|decree)",
            r"agrees? to pay .* (?:penalt|fine)",
            r"civil penalty",
            r"settles? .* charges",
        ],
    ),
    "regulatory_investigation": (
        "medium",
        [
            r"under investigation",
            r"probe\w*",
            r"subpoena\w*",
            r"regulators? (?:investigat|scrutin)\w*",
            r"wells notice",
            r"inquiry into",
        ],
    ),
    "litigation": (
        "medium",
        [r"lawsuit\w*", r"\bsued\b", r"\bsues\b", r"class[- ]action", r"antitrust", r"litigation"],
    ),
    "cybersecurity": (
        "medium",
        [r"data breach\w*", r"\bhack\w*", r"ransomware", r"cyber[- ]?attack\w*", r"leak\w* (?:customer|personal) data"],
    ),
    "labor_human_rights": ("medium", [r"forced labou?r", r"child labou?r", r"human trafficking", r"modern slavery"]),
    "safety_environment": (
        "medium",
        [r"environmental (?:violation|crime)\w*", r"toxic spill", r"safety violation\w*", r"recall\w*"],
    ),
}
_COMPILED = {k: (sev, [re.compile(rf"\b{p}", re.I) for p in pats]) for k, (sev, pats) in CATEGORIES.items()}
SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}
GENERIC = {
    "inc",
    "corp",
    "corporation",
    "company",
    "co",
    "ltd",
    "limited",
    "plc",
    "llc",
    "group",
    "holdings",
    "holding",
    "the",
    "of",
    "and",
    "public",
    "joint",
    "stock",
    "pjsc",
    "jsc",
    "sa",
    "ag",
    "nv",
    "international",
}


@dataclass
class MediaClassification:
    relevant: bool
    categories: list[str]
    severity: str
    matched_terms: list[str]
    version: str = TAXONOMY_VERSION


def distinctive_tokens(name: str) -> list[str]:
    toks = re.findall(r"[a-z0-9]+", name.lower())
    return [t for t in toks if t not in GENERIC and len(t) > 1]


def classify(text: str, subject_names: list[str]) -> MediaClassification:
    low = text.lower()
    relevant = False
    for n in subject_names:
        toks = distinctive_tokens(n)
        if toks and all(re.search(rf"\b{re.escape(t)}\b", low) for t in toks[:2]):
            relevant = True
            break
    cats, terms, sev = [], [], "none"
    for cat, (csev, pats) in _COMPILED.items():
        hits = [m.group(0) for p in pats if (m := p.search(text))]
        if hits:
            cats.append(cat)
            terms += hits
            if SEVERITY_RANK[csev] > SEVERITY_RANK[sev]:
                sev = csev
    return MediaClassification(relevant, cats, sev if cats else "none", sorted(set(terms)))


# ---------------------------------------------------------------------------------------------- subject role
_DEF_VERBS = (
    r"sues|sued|charges|charged|indicts|indicted|fines|fined|penalizes|sanctions|sanctioned|investigates|probes|"
    r"files (?:suit|a lawsuit|an antitrust lawsuit|charges) against|settles with|reaches (?:a )?settlement with|"
    r"obtains (?:a )?(?:judgment|consent decree|guilty plea) (?:against|from)|announces charges against|"
    r"secures .{0,40} from|takes action against|blocks|designates|"
    r"(?:suit|lawsuit|case|charges|action|complaint|indictment) against"
)
_SUBJ_VERBS = (
    r"agrees? to pay|to pay|pays|paid|pleads? guilty|pleaded guilty|admits|is fined|fined|charged|indicted|settles?|"
    r"sentenced|ordered to pay|accused|faces (?:charges|lawsuit|probe)|to plead guilty|convicted|violated|"
    r"resolves? (?:criminal|civil)"
)
_VICTIM = (
    r"defraud\w*|rob\w*|steal\w*|stole|stolen|counterfeit\w*|fake|targeting|hijack\w*|impersonat\w*|"
    r"(?:scheme|fraud|scam) (?:against|targeting|involving)|selling stolen|trafficker of counterfeit"
)
_LEGAL = r"(?:\s+(?:inc|incorporated|corp|corporation|co|company|ltd|limited|plc|llc|group))?\.?"


def subject_role(title: str, subject_names: list[str]) -> str:
    """Return 'subject' (entity is the target of the action), 'victim', or 'mentioned' (role unclear)."""
    t = " ".join(title.split())
    for n in subject_names:
        toks = distinctive_tokens(n)
        if not toks:
            continue
        name = r"\b" + r"\W+".join(re.escape(x) for x in toks[:3]) + r"\b" + _LEGAL
        if re.search(rf"(?:{_VICTIM})\W+(?:\w+\W+){{0,3}}?{name}", t, re.I) or re.search(
            rf"{name}\s+(?:products?|devices?|iphones?|ipads?|gift cards?|stores?|customers?|employees?|accounts?)\b",
            t,
            re.I,
        ):
            return "victim"
        if re.search(rf"(?:{_DEF_VERBS})\s+(?:\w+\s+){{0,3}}?{name}", t, re.I) or re.search(
            rf"{name}\s*,?\s+(?:{_SUBJ_VERBS})\b", t, re.I
        ):
            return "subject"
    return "mentioned"
