"""Escalation gate — R5: frontier/cloud calls need explicit per-step approval.

Flow: scan() flags PII → request_cloud_call() refuses unless approved=True
(and even then logs). Zero bytes leave before approval; every decision —
allowed or blocked — lands in the audit log.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .audit import AuditLog
from .pii import PIIDecision, scan


@dataclass
class CallResult:
    allowed: bool
    bytes_sent: int


class Gate:
    def __init__(self, audit_path: Path | str):
        self.audit = AuditLog(audit_path)

    def scan(self, text: str) -> PIIDecision:
        return scan(text)

    def request_cloud_call(self, payload: dict, approved: bool) -> CallResult:
        if not approved:
            self.audit.append(
                {
                    "event": "cloud_call_blocked",
                    "reason": "unapproved",
                    "model": payload.get("model"),
                    "payload_bytes": len(str(payload)),
                }
            )
            return CallResult(allowed=False, bytes_sent=0)
        decision = self.scan(str(payload))
        self.audit.append(
            {
                "event": "cloud_call_allowed",
                "approved": True,
                "model": payload.get("model"),
                "pii_categories": decision.categories,
            }
        )
        return CallResult(allowed=True, bytes_sent=len(str(payload)))
