"""PII prefilter — deterministic rule-based scan, first stage of the gate.

Second stage (PII-Tracer-GGUF) plugs in behind scan() later; the rule-based
layer stays as the deterministic floor regardless of model availability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PATTERNS: dict[str, re.Pattern] = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    "credential": re.compile(
        r"\b(?:sk|pk|api[_-]?key|token|bearer)[-_][\w-]{12,}\b|sk-or-v1-[\w-]+",
        re.IGNORECASE,
    ),
    "card": re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b"),
    "address": re.compile(
        r"\b\d+\s+[A-Z][\w.]+\s+(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Terrace|Boulevard|Blvd)\b"
    ),
}


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        total += n
    return total % 10 == 0


@dataclass
class PIIDecision:
    flagged: bool
    categories: list[str] = field(default_factory=list)


def scan(text: str) -> PIIDecision:
    cats = []
    for cat, pat in _PATTERNS.items():
        for m in pat.finditer(text):
            if cat == "card" and not _luhn_ok(re.sub(r"\D", "", m.group())):
                continue
            cats.append(cat)
            break
    return PIIDecision(flagged=bool(cats), categories=cats)
