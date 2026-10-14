"""Approval queue — pending escalations for the human console.

File-backed NDJSON (one event per line, append-only like the audit log):
  {"event": "request", "id", "ts", "who", "session", "what", "model", "egress",
   "est_cost", "pii", "payload_hash", "payload_bytes", "payload"}
  {"event": "decision", "id", "ts", "decision": "approved"|"sent_back", "by", "note"}

Status is derived, never stored. Deciding goes through Gate: approve writes a
human_decision that is an approval bound to this payload's hash (single use,
expiring); send-back writes human_decision then cloud_call_blocked. Approving
does NOT send: the caller that owns the payload later presents the approval to
Gate.request_cloud_call, which is the only path that lets bytes leave.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from .gate import Gate, payload_hash  # noqa: F401  (payload_hash re-exported: the one binding hash)


class QueueError(Exception):
    """Refused decision: unknown id, already decided, stale hash, missing note."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


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
        est_cost: float = 0.0, ts: float | None = None, item_id: str | None = None,
        bound_hash: str | None = None,
    ) -> str:
        decision = self.gate.scan(str(payload))
        egress = str(payload.get("egress", "<unset>"))
        if item_id is None:
            item_id = "ap_" + uuid.uuid4().hex[:8]
        elif self.get(item_id) is not None:
            raise QueueError(f"{item_id} already exists. A changed payload is a new request.", 409)
        self._append({
            "event": "request", "id": item_id, "ts": ts if ts is not None else time.time(),
            "who": who, "session": session, "what": what,
            "model": payload.get("model", "<unset>"), "egress": egress,
            "est_cost": est_cost, "pii": decision.categories,
            "payload_hash": bound_hash or payload_hash(payload),
            "payload_bytes": len(str(payload)), "payload": payload,
        })
        return item_id

    def supersede(self, item_id: str, *, by: str = "system") -> None:
        """A changed payload is a new request: close the old one so it can never be approved."""
        item = self.get(item_id)
        if item is None or item["status"] != "pending":
            raise QueueError("Only a pending approval can be superseded.", 409)
        self._append({"event": "decision", "id": item_id, "ts": time.time(), "decision": "superseded", "by": by, "note": ""})

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
        if approve:
            self.gate.grant(payload, by=by, approval_id=item_id, note=note)
        else:
            self.gate.deny(payload, by=by, approval_id=item_id, note=note)
        self._append({
            "event": "decision", "id": item_id, "ts": time.time(),
            "decision": "approved" if approve else "sent_back", "by": by, "note": note,
        })
        return {"id": item_id, "approved": approve, "allowed": approve, "sent": False,
                "decision": "approved" if approve else "sent_back"}
