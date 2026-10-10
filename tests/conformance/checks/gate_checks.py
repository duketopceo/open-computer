"""R5 (per-step escalation approval), PII gate, audit checks.

Adapter factory: services.gate.adapter(tmp_path)
"""

from __future__ import annotations

import json
from pathlib import Path

from harness import CheckFailed, check

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _jsonl(path: Path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


@check("R5", "labeled PII fixtures flagged before any cloud call")
def pii_labeled_flagged(gate) -> None:
    for row in _jsonl(FIXTURES / "pii_labeled.jsonl"):
        dec = gate.scan(row["text"])
        if row["expect_flagged"] and not dec.flagged:
            raise CheckFailed(f"PII not flagged: {row['id']}")
        if not row["expect_flagged"] and dec.flagged:
            raise CheckFailed(f"false positive: {row['id']}")


@check("R5", "unapproved cloud call blocked; zero bytes egress; audit row exists")
def unapproved_cloud_call_blocked(gate) -> None:
    res = gate.request_cloud_call(
        {"model": "frontier", "payload": "sensitive"}, approved=False
    )
    if res.allowed:
        raise CheckFailed("unapproved cloud call allowed")
    if res.bytes_sent != 0:
        raise CheckFailed(f"bytes left before approval: {res.bytes_sent}")
    if not gate.audit.rows():
        raise CheckFailed("blocked call left no audit row")


@check("R5", "audit log is append-only")
def audit_append_only(gate) -> None:
    gate.audit.append({"event": "meta-check"})
    try:
        gate.audit.delete(0)
    except Exception:
        return
    raise CheckFailed("audit row deleted — log is mutable")
