"""Secret and PII redaction, applied at the logging/record sink so no call site has to remember.

Three layers: (1) key names that mean "secret", (2) value shapes that look like credentials or PII,
(3) the literal values of environment variables whose names look secret. Over-redaction is the safe failure.
"""

from __future__ import annotations

import os
import re

MASK = "[REDACTED]"

_SECRET_PARTS = {
    "secret", "secrets", "password", "passwd", "authorization", "cookie", "cookies", "credential",
    "credentials", "apikey", "dsn", "bearer", "token", "passphrase", "signature",
}
_PAIRS = {("api", "key"), ("private", "key"), ("access", "key"), ("secret", "key"), ("auth", "key")}

_VALUE_PATTERNS = [
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),  # OpenAI/OpenRouter-style keys (sk-or-v1-...)
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"),  # JWT
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"),  # user:password@host
    re.compile(r"(?i)(?<=[?&])(?:api_?key|key|token|access_token|secret|password)=[^&\s]+"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),  # email
    re.compile(r"(?<![\d.])(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)"),  # phone
]

_MAX_DEPTH = 8


def sensitive_key(key: object) -> bool:
    parts = [p for p in re.split(r"[^a-z0-9]+", str(key).lower()) if p]
    if not parts:
        return False
    if parts[-1] == "key" or any(p in _SECRET_PARTS for p in parts):
        return True
    return any((a, b) in _PAIRS for a, b in zip(parts, parts[1:]))


def _env_secrets() -> list[str]:
    out = []
    for name, val in os.environ.items():
        if len(val) >= 8 and sensitive_key(name):
            out.append(val)
    out.sort(key=len, reverse=True)
    return out


def redact_str(s: str, _secrets: list[str] | None = None) -> str:
    for v in _env_secrets() if _secrets is None else _secrets:
        if v in s:
            s = s.replace(v, MASK)
    for pat in _VALUE_PATTERNS:
        s = pat.sub(MASK, s)
    return s


def redact(obj, _depth: int = 0, _secrets: list[str] | None = None):
    """Return a redacted copy of any JSON-ish structure. Never mutates the input."""
    secrets = _env_secrets() if _secrets is None else _secrets
    if _depth > _MAX_DEPTH:
        return "[TRUNCATED]"
    if isinstance(obj, str):
        return redact_str(obj, secrets)
    if isinstance(obj, dict):
        return {
            k: (MASK if sensitive_key(k) and v is not None and v != "" else redact(v, _depth + 1, secrets))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple, set)):
        return [redact(v, _depth + 1, secrets) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        return f"<{len(obj)} bytes>"
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    return redact_str(str(obj), secrets)
