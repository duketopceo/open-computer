"""Approval queue — pending escalations for the human console.

File-backed NDJSON (one event per line, append-only like the audit log):
  {"event": "request", "id", "ts", "who", "session", "what", "model", "egress",
   "est_cost", "pii", "payload_hash", "payload_bytes", "payload"}
  {"event": "decision", "id", "ts", "decision": "approved"|"sent_back", "by", "note"}

Status is derived, never stored. Deciding writes the audit rows through Gate
(human_decision, then cloud_call_allowed / cloud_call_blocked), so the audit
log stays the single record of what was allowed to leave.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from .gate import Gate


class QueueError(Exception):
    """Refused decision: unknown id, already decided, stale hash, missing note."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def payload_hash(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


class ApprovalQueue:
    def __init__(self, path: Path | str, gate: Gate | None = None):
        self.path = Path(path)
        self.gate = gate

    # --- reads (no side effects: never creates the file) ------------------
    def _events(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def items(self) -> list[dict]:
        """All requests with derived status, oldest first."""
        reqs: dict[str, dict] = {}
        for e in self._events():
            if e.get("event") == "request":
                reqs[e["id"]] = {**e, "status": "pending"}
            elif e.get("event") == "decision" and e.get("id") in reqs:
                r = reqs[e["id"]]
                r["status"] = e["decision"]
                r["decided_by"] = e.get("by")
                r["decided_ts"] = e.get("ts")
                r["note"] = e.get("note", "")
        return sorted(reqs.values(), key=lambda r: r["ts"])

    def pending(self) -> list[dict]:
        return [r for r in self.items() if r["status"] == "pending"]

    def get(self, item_id: str) -> dict | None:
        return next((r for r in self.items() if r["id"] == item_id), None)

    # --- writes ------------------------------------------------------------
    def _append(self, row: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(row) + "\n")

    def submit(
        self, *, who: str, what: str, payload: dict, session: str = "",
        est_cost: float = 0.0, ts: float | None = None,
    ) -> str:
        decision = self.gate.scan(str(payload))
        egress = str(payload.get("egress", "<unset>"))
        item_id = "ap_" + uuid.uuid4().hex[:8]
        self._append({
            "event": "request", "id": item_id, "ts": ts if ts is not None else time.time(),
            "who": who, "session": session, "what": what,
            "model": payload.get("model", "<unset>"), "egress": egress,
            "est_cost": est_cost, "pii": decision.categories,
            "payload_hash": payload_hash(payload),
            "payload_bytes": len(str(payload)), "payload": payload,
        })
        return item_id

    def decide(
        self, item_id: str, *, approve: bool, by: str, note: str = "",
        expect_hash: str | None = None,
    ) -> dict:
        assert self.gate is not None, "deciding needs a Gate"
        item = self.get(item_id)
        if item is None:
            raise QueueError("No such approval.", 404)
        if item["status"] != "pending":
            raise QueueError(f"Already {item['status'].replace('_', ' ')}.", 409)
        if expect_hash is not None and expect_hash != item["payload_hash"]:
            raise QueueError(
                "The request changed since you opened it. Reload and review it again.", 409
            )
        note = note.strip()
        if not approve and not note:
            raise QueueError("Add a note so the agent knows what to change.", 422)
        payload = item["payload"]
        self.gate.audit.append({
            "event": "human_decision", "approved": approve, "model": item["model"],
            "approval_id": item_id, "by": by, "payload_hash": item["payload_hash"],
            **({"note": note} if note else {}),
        })
        # The gate writes cloud_call_allowed / cloud_call_blocked and is the only
        # thing that may authorise bytes leaving the box.
        result = self.gate.request_cloud_call(payload, approved=approve)
        self._append({
            "event": "decision", "id": item_id, "ts": time.time(),
            "decision": "approved" if approve else "sent_back", "by": by, "note": note,
        })
        return {"id": item_id, "allowed": result.allowed,
                "decision": "approved" if approve else "sent_back"}
