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


class Spy:
    """Stands in for the real egress. The only evidence of 'bytes left' is this being called."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def __call__(self, payload: dict) -> int:
        self.sent.append(payload)
        return len(str(payload))


PAYLOAD_A = {"model": "frontier", "payload": "summarise the release notes"}
PAYLOAD_B = {"model": "frontier", "payload": "summarise the release notes AND the customer list"}
PAYLOAD_PII = {"model": "frontier", "payload": "reply to jane@example.com about SSN 123-45-6789"}


def _events(gate) -> list[str]:
    return [r.get("event") for r in gate.audit.rows()]


@check("R5", "unapproved or forged approval: blocked, sender never called, audit row exists")
def unapproved_cloud_call_blocked(gate) -> None:
    spy = Spy()
    res = gate.request_cloud_call(PAYLOAD_A, None, sender=spy)
    if res.allowed or res.bytes_sent != 0 or spy.sent:
        raise CheckFailed("unapproved cloud call let bytes out")
    if "cloud_call_blocked" not in _events(gate):
        raise CheckFailed("blocked call left no audit row")
    forged = gate.approval_for(PAYLOAD_A, "ap_forged")  # a made-up claim, no human decision behind it
    res = gate.request_cloud_call(PAYLOAD_A, forged, sender=spy)
    if res.allowed or spy.sent:
        raise CheckFailed("forged approval accepted: approval is a caller-supplied value")


@check("R5", "approval is bound to the payload hash")
def approval_bound_to_payload(gate) -> None:
    spy = Spy()
    approval = gate.grant(PAYLOAD_A, by="human")
    res = gate.request_cloud_call(PAYLOAD_B, approval, sender=spy)
    if res.allowed or spy.sent:
        raise CheckFailed("approval for payload A released payload B")
    res = gate.request_cloud_call(PAYLOAD_A, approval, sender=spy)
    if not res.allowed or spy.sent != [PAYLOAD_A]:
        raise CheckFailed("approved payload was not sent exactly once, unmodified")
    if res.bytes_sent != len(str(PAYLOAD_A)):
        raise CheckFailed("bytes_sent does not match what the sender reported")


@check("R5", "approval is single use")
def approval_single_use(gate) -> None:
    spy = Spy()
    approval = gate.grant(PAYLOAD_A, by="human")
    first = gate.request_cloud_call(PAYLOAD_A, approval, sender=spy)
    replay = gate.request_cloud_call(PAYLOAD_A, approval, sender=spy)
    if not first.allowed:
        raise CheckFailed("valid approval refused")
    if replay.allowed or len(spy.sent) != 1:
        raise CheckFailed("approval replayed: payload sent twice on one approval")


@check("R5", "approval expires")
def approval_expires(gate) -> None:
    spy = Spy()
    approval = gate.grant(PAYLOAD_A, by="human")
    gate.clock = lambda: 4_000_000_000.0  # far past any ttl
    res = gate.request_cloud_call(PAYLOAD_A, approval, sender=spy)
    if res.allowed or spy.sent:
        raise CheckFailed("expired approval still released the payload")


@check("R5", "no egress configured: nothing leaves and the approval is not spent")
def no_sender_fails_closed(gate) -> None:
    approval = gate.grant(PAYLOAD_A, by="human")
    res = gate.request_cloud_call(PAYLOAD_A, approval, sender=None)
    if res.allowed or res.bytes_sent:
        raise CheckFailed("gate reported a send with no egress configured")
    spy = Spy()
    if not gate.request_cloud_call(PAYLOAD_A, approval, sender=spy).allowed:
        raise CheckFailed("approval was consumed by a call that sent nothing")


@check("R5", "approved send with PII is allowed explicitly and audited with the categories")
def pii_send_is_explicit(gate) -> None:
    spy = Spy()
    approval = gate.grant(PAYLOAD_PII, by="human")
    res = gate.request_cloud_call(PAYLOAD_PII, approval, sender=spy)
    if not res.allowed:
        raise CheckFailed("human-approved payload refused")
    row = next((r for r in gate.audit.rows() if r.get("event") == "cloud_call_allowed"), None)
    if row is None or not {"email", "ssn"} <= set(row.get("pii_categories", [])):
        raise CheckFailed("allowed send with PII did not record the PII categories")


@check("R5", "audit log is append-only")
def audit_append_only(gate) -> None:
    gate.audit.append({"event": "meta-check"})
    try:
        gate.audit.delete(0)
    except Exception:
        return
    raise CheckFailed("audit row deleted — log is mutable")
