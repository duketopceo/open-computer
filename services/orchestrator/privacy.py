"""Hold a goal or step whose text has private categories until the owner sends or skips it.

The card is an egress-style wait: nothing is mailed and no model runs while it is pending.
Approve consumes one hash-bound grant and sends that payload to the effort model once.
Send-back on this kind is skip. An egress approval does not satisfy it.
"""

from __future__ import annotations

import os
from pathlib import Path

from services.gate import Gate, payload_hash
from services.gate.pii import scan
from services.gate.queue import ApprovalQueue, default_approvals_path

KIND = "pii-send"
WHO = "orchestrator"


def payload_for(run_id: str, text: str, model: str, *, step: str | None = None) -> dict:
    """The hashed object: the exact text plus the effort model id."""
    body = {"kind": KIND, "run": run_id, "model": model, "text": text}
    if step is not None:
        body["step"] = step
    return body


class PrivacyHold:
    """One approvals queue and the gate that consumes a grant on send."""

    def __init__(self, queue: ApprovalQueue, gate: Gate):
        self.queue = queue
        self.gate = gate

    def categories(self, text: str) -> list[str]:
        return list(scan(text).categories)

    def request(self, run_id: str, text: str, model: str, *, step: str | None = None) -> tuple[str, dict]:
        payload = payload_for(run_id, text, model, step=step)
        where = f"step {step}" if step else "the planner"
        aid = self.queue.submit(
            who=WHO,
            what=f"pii-send: send {where} of run {run_id} to {model}",
            payload=payload,
            session=run_id,
        )
        return aid, payload

    def status(self, approval_id: str) -> str:
        return "missing" if self.queue.get(approval_id) is None else self.queue.get(approval_id)["status"]

    def note(self, approval_id: str) -> str:
        item = self.queue.get(approval_id)
        return "" if item is None else str(item.get("note") or "")

    def cancel(self, approval_id: str) -> None:
        self.queue.supersede(approval_id, by=WHO)

    def consume(self, payload: dict, approval_id: str, sender):
        """One send of this payload. A consumed or expired grant does not call `sender`."""
        approval = self.gate.approval_for(payload, approval_id)
        return self.gate.request_cloud_call(payload, approval, sender=sender)

    def hash_of(self, payload: dict) -> str:
        return payload_hash(payload)


def live_privacy() -> PrivacyHold:
    """The console queue and audit, so an owner decision is the one the run consumes."""
    audit = Path(os.environ.get("OC_AUDIT", "/tmp/oc-audit/audit.ndjson"))
    gate = Gate(audit)
    return PrivacyHold(ApprovalQueue(default_approvals_path(), gate), gate)
