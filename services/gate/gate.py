"""Escalation gate - R5: frontier/cloud calls need explicit per-step approval.

The gate is the only code path that may let a payload leave the box.

  grant()                 human path: records an approval bound to the SHA-256 of one exact payload
  deny()                  human path: records a refusal
  request_cloud_call()    the single send path: verifies the approval, consumes it, then calls `sender`

An approval is a row in the audit log, not a flag the caller supplies:
  * bound to the payload hash: an approval for payload A never releases payload B
  * single use: the consuming row (`cloud_call_allowed`) is written BEFORE the sender runs, so a crash
    mid-send cannot be replayed; a second request with the same approval is refused
  * expiring: refused after `approval_ttl` seconds
  * unforgeable by the caller: a made-up `Approval(id, hash)` with no human_decision row is refused
  * fail closed: no `sender` configured means nothing leaves and the approval is not consumed
Every outcome, allowed or blocked, lands in the audit log, including the PII categories of the payload.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from .audit import AuditLog
from .pii import PIIDecision, scan

DEFAULT_APPROVAL_TTL = 900.0  # seconds


def payload_hash(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


@dataclass(frozen=True)
class Approval:
    """A claim check. Validity is decided by the gate from its own audit log, never from these fields."""

    id: str
    payload_hash: str


@dataclass
class CallResult:
    allowed: bool
    bytes_sent: int
    reason: str = ""


Sender = Callable[[dict], int]


class Gate:
    def __init__(
        self,
        audit_path: Path | str,
        *,
        clock: Callable[[], float] = time.time,
        approval_ttl: float = DEFAULT_APPROVAL_TTL,
    ):
        self.audit = AuditLog(audit_path)
        self.clock = clock
        self.approval_ttl = approval_ttl

    def scan(self, text: str) -> PIIDecision:
        return scan(text)

    def approval_for(self, payload: dict, approval_id: str) -> Approval:
        """An unverified claim for `payload`. Holding one proves nothing; the gate checks its log."""
        return Approval(id=approval_id, payload_hash=payload_hash(payload))

    # --- human path ---------------------------------------------------------
    def grant(self, payload: dict, *, by: str = "human", approval_id: str | None = None, note: str = "") -> Approval:
        aid = approval_id or "ap_" + uuid.uuid4().hex[:8]
        with self._locked():
            if self._decision_row(aid) is not None:
                raise ValueError(f"approval {aid} already decided")
            h = payload_hash(payload)
            self.audit.append(
                {
                    "event": "human_decision",
                    "approved": True,
                    "model": payload.get("model"),
                    "approval_id": aid,
                    "by": by,
                    "payload_hash": h,
                    "expires": self.clock() + self.approval_ttl,
                    "pii_categories": self.scan(str(payload)).categories,
                    **({"note": note} if note else {}),
                }
            )
        return Approval(id=aid, payload_hash=h)

    def deny(self, payload: dict, *, by: str = "human", approval_id: str | None = None, note: str = "") -> CallResult:
        aid = approval_id or "ap_" + uuid.uuid4().hex[:8]
        with self._locked():
            if self._decision_row(aid) is not None:
                raise ValueError(f"approval {aid} already decided")
            h = payload_hash(payload)
            self.audit.append(
                {
                    "event": "human_decision",
                    "approved": False,
                    "model": payload.get("model"),
                    "approval_id": aid,
                    "by": by,
                    "payload_hash": h,
                    **({"note": note} if note else {}),
                }
            )
            self._blocked(payload, "sent_back", aid)
        return CallResult(allowed=False, bytes_sent=0, reason="sent_back")

    # --- the single send path ----------------------------------------------
    def request_cloud_call(
        self, payload: dict, approval: Approval | None = None, *, sender: Sender | None = None
    ) -> CallResult:
        with self._locked():
            reason = self._refusal(payload, approval)
            if reason is None and sender is None:
                reason = "no_egress_configured"  # nothing can leave; approval stays unconsumed
            if reason is not None:
                self._blocked(payload, reason, approval.id if approval else None)
                return CallResult(allowed=False, bytes_sent=0, reason=reason)
            assert approval is not None and sender is not None
            # Consume first: if the process dies mid-send the approval is spent, never replayable.
            self.audit.append(
                {
                    "event": "cloud_call_allowed",
                    "approved": True,
                    "model": payload.get("model"),
                    "approval_id": approval.id,
                    "payload_hash": payload_hash(payload),
                    "payload_bytes": len(str(payload)),
                    "pii_categories": self.scan(str(payload)).categories,
                }
            )
        try:
            sent = int(sender(payload))
        except Exception as e:  # the approval is already spent; record the failure, send nothing more
            self.audit.append({"event": "cloud_call_failed", "approval_id": approval.id, "error": type(e).__name__})
            return CallResult(allowed=True, bytes_sent=0, reason="sender_error")
        return CallResult(allowed=True, bytes_sent=sent)

    # --- checks (overridable hooks so mutation tests can break exactly one) ---
    def _refusal(self, payload: dict, approval: Approval | None) -> str | None:
        if approval is None:
            return "unapproved"
        row = self._decision_row(approval.id)
        if row is None or not row.get("approved"):
            return "unknown_approval"
        if not self._hash_matches(row, payload):
            return "payload_mismatch"
        if self._is_consumed(approval.id):
            return "replayed"
        if self._is_expired(row):
            return "expired"
        return None

    def _hash_matches(self, row: dict, payload: dict) -> bool:
        return row.get("payload_hash") == payload_hash(payload)

    def _is_consumed(self, approval_id: str) -> bool:
        return any(
            r.get("event") == "cloud_call_allowed" and r.get("approval_id") == approval_id for r in self.audit.rows()
        )

    def _is_expired(self, row: dict) -> bool:
        return self.clock() > float(row.get("expires", 0))

    def _decision_row(self, approval_id: str) -> dict | None:
        return next(
            (
                r
                for r in self.audit.rows()
                if r.get("event") == "human_decision" and r.get("approval_id") == approval_id
            ),
            None,
        )

    def _blocked(self, payload: dict, reason: str, approval_id: str | None) -> None:
        self.audit.append(
            {
                "event": "cloud_call_blocked",
                "reason": reason,
                "model": payload.get("model"),
                "payload_bytes": len(str(payload)),
                **({"approval_id": approval_id} if approval_id else {}),
            }
        )

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Serialise check-and-consume across threads and processes (flock on a sidecar file)."""
        with open(str(self.audit.path) + ".lock", "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
