"""Console approvals for agent jobs: a durable store, the approver a job thread waits on, and the job lease.

Append-only NDJSON (one event per line, status derived, never stored):
  {"event": "request", "id": "ag_xxxxxxxx", "ts", "job", "kind", "detail", "hash", "expires_at"}
  {"event": "attach",  "id", "ts", "expires_at"}      a resumed approver re-attached: the TTL clock restarts
  {"event": "decision", "id", "ts", "decision": "approved"|"denied"|"expired", "by", "note"}
  {"event": "consume", "id", "ts"}                    the loop took the answer: single use

The hash is payload_hash({job, kind, detail}); a decision must carry it, and it must also match the stored
payload recomputed, so an edited store cannot smuggle a different payload under a reviewed hash.
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from services.gate.gate import payload_hash

DEFAULT_TTL_S = 86400.0


class ApprovalError(Exception):
    """Refused: unknown id (404), already decided / stale or tampered hash / expired (409), bad input (422)."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def default_ttl_s() -> float:
    try:
        v = float(os.environ.get("OC_AGENT_APPROVAL_TTL_S", DEFAULT_TTL_S))
    except ValueError:
        return DEFAULT_TTL_S
    return v if v > 0 else DEFAULT_TTL_S


def request_hash(job: str, kind: str, detail: dict) -> str:
    return payload_hash({"job": job, "kind": kind, "detail": detail})


class ApprovalStore:
    def __init__(self, path: Path | str, ttl_s: float | None = None, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.ttl_s = float(ttl_s) if ttl_s is not None else default_ttl_s()
        self.clock = clock
        self._tlock = threading.Lock()

    # --- reads (never create the file) -------------------------------------
    def _events(self) -> list[dict]:
        try:
            text = self.path.read_text()
        except OSError:
            return []
        out = []
        for line in text.splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                out.append(e)
        return out

    def _fold(self, events: list[dict], now: float) -> dict[str, dict]:
        reqs: dict[str, dict] = {}
        for e in events:
            rid = e.get("id")
            if e.get("event") == "request":
                reqs[rid] = {"id": rid, "job": e.get("job"), "kind": e.get("kind"), "detail": e.get("detail"),
                             "hash": e.get("hash"), "ts": e.get("ts"), "expires_at": e.get("expires_at"),
                             "decision": None, "by": None, "note": "", "consumed": False}
            elif rid in reqs:
                r = reqs[rid]
                if e.get("event") == "attach":
                    r["expires_at"] = e.get("expires_at")
                elif e.get("event") == "decision" and r["decision"] is None:  # first decision wins
                    r["decision"], r["by"], r["note"] = e.get("decision"), e.get("by"), e.get("note", "")
                elif e.get("event") == "consume":
                    r["consumed"] = True
        for r in reqs.values():
            if r["decision"] is not None:
                r["status"] = r["decision"]
            elif now >= float(r["expires_at"] or 0):
                r["status"] = "expired"  # lazy TTL: never actionable, even before anyone records it
            else:
                r["status"] = "pending"
        return reqs

    def items(self, job: str | None = None) -> list[dict]:
        rows = self._fold(self._events(), self.clock()).values()
        return sorted((r for r in rows if job is None or r["job"] == job), key=lambda r: r["ts"])

    def get(self, rid: str) -> dict | None:
        return self._fold(self._events(), self.clock()).get(rid)

    def pending(self, job: str | None = None) -> list[dict]:
        return [{k: r[k] for k in ("id", "job", "kind", "detail", "hash", "ts", "expires_at")}
                for r in self.items(job) if r["status"] == "pending"]

    def _check(self, reqs: dict[str, dict], rid: str, hash_: object) -> dict:
        """The shared gate for decide and verify. Raises; never writes."""
        if not isinstance(hash_, str) or not hash_:
            raise ApprovalError("a payload hash is required", 422)
        r = reqs.get(rid)
        if r is None:
            raise ApprovalError(f"unknown approval {rid}", 404)
        if r["status"] == "expired" and r["decision"] is None:
            raise ApprovalError(f"{rid} expired", 409)
        if r["status"] != "pending":
            raise ApprovalError(f"{rid} is already {r['status']}", 409)
        if hash_ != r["hash"]:
            raise ApprovalError("payload hash is stale: the request changed since you reviewed it", 409)
        if request_hash(r["job"], r["kind"], r["detail"]) != r["hash"]:
            raise ApprovalError("stored payload does not match its hash", 409)
        return r

    def verify(self, rid: str, hash_: str) -> dict:
        """Read-only: pending, unexpired, hash matches caller and stored payload. For a console side effect."""
        return self._check(self._fold(self._events(), self.clock()), rid, hash_)

    # --- writes ------------------------------------------------------------
    @contextmanager
    def _txn(self) -> Iterator[Callable[[dict], None]]:
        with self._tlock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield lambda row: os.write(fd, (json.dumps(row) + "\n").encode())
            finally:
                os.close(fd)  # closing drops the flock

    def open(self, job: str, kind: str, detail: dict, ttl_s: float | None = None, now: float | None = None) -> str:
        """Attach to an unconsumed request with the same job/kind/hash, or create one. Returns its id."""
        h = request_hash(job, kind, detail)
        now = self.clock() if now is None else now
        exp = now + (self.ttl_s if ttl_s is None else float(ttl_s))
        with self._txn() as write:
            reqs = self._fold(self._events(), now)
            for r in reqs.values():
                if (r["job"], r["kind"], r["hash"]) == (job, kind, h) and not r["consumed"] \
                        and r["decision"] in (None, "approved", "denied"):
                    if r["decision"] is None:
                        write({"event": "attach", "id": r["id"], "ts": now, "expires_at": exp})
                    return r["id"]
            rid = "ag_" + uuid.uuid4().hex[:8]
            while rid in reqs:
                rid = "ag_" + uuid.uuid4().hex[:8]
            write({"event": "request", "id": rid, "ts": now, "job": job, "kind": kind, "detail": detail,
                   "hash": h, "expires_at": exp})
            return rid

    def decide(self, rid: str, approve: bool, by: str, hash: str, note: str = "") -> dict:
        if not isinstance(by, str) or not by.strip():
            raise ApprovalError("a decider is required", 422)
        with self._txn() as write:
            now = self.clock()
            reqs = self._fold(self._events(), now)
            try:
                self._check(reqs, rid, hash)
            except ApprovalError as e:
                r = reqs.get(rid)
                if r and r["status"] == "expired" and r["decision"] is None:
                    write({"event": "decision", "id": rid, "ts": now, "decision": "expired", "by": "ttl", "note": ""})
                raise e
            write({"event": "decision", "id": rid, "ts": now, "decision": "approved" if approve else "denied",
                   "by": by, "note": str(note)[:500]})
        return self.get(rid)

    def expire_due(self, rid: str, now: float | None = None) -> None:
        """Record `expired` for a request whose TTL has passed and that nobody decided."""
        with self._txn() as write:
            now = self.clock() if now is None else now
            r = self._fold(self._events(), now).get(rid)
            if r and r["decision"] is None and r["status"] == "expired":
                write({"event": "decision", "id": rid, "ts": now, "decision": "expired", "by": "ttl", "note": ""})

    def consume(self, rid: str, now: float | None = None) -> None:
        with self._txn() as write:
            write({"event": "consume", "id": rid, "ts": self.clock() if now is None else now})

    def void_unresolved(self, job: str, keep_kinds: tuple[str, ...] = ("plan",)) -> list[str]:
        """Resume-void (R6): expire the job's undecided requests whose round the resumed planner regenerates."""
        voided = []
        with self._txn() as write:
            now = self.clock()
            for r in self._fold(self._events(), now).values():
                if r["job"] == job and r["decision"] is None and r["kind"] not in keep_kinds:
                    write({"event": "decision", "id": r["id"], "ts": now, "decision": "expired", "by": "resume",
                           "note": "voided on resume"})
                    voided.append(r["id"])
        return voided


class ConsoleApprover:
    """The loop's `approve` hook for one job: (kind, detail) -> bool, blocking until the console decides."""

    def __init__(self, store: ApprovalStore, job: str, poll_s: float = 2.0, ttl_s: float | None = None,
                 clock: Callable[[], float] | None = None, sleep: Callable[[float], None] = time.sleep):
        self.store, self.job, self.poll_s, self.ttl_s, self.sleep = store, job, poll_s, ttl_s, sleep
        self.clock = clock or store.clock

    def __call__(self, kind: str, detail: dict) -> bool:
        rid = self.store.open(self.job, kind, detail, ttl_s=self.ttl_s, now=self.clock())
        while True:
            r = self.store.get(rid)
            if r is None:
                return False
            if r["decision"] is None and self.clock() >= float(r["expires_at"] or 0):
                self.store.expire_due(rid, now=self.clock())
                continue
            if r["decision"] is not None:
                self.store.consume(rid)
                return r["decision"] == "approved"
            self.sleep(self.poll_s)


@contextmanager
def lease(path: Path | str) -> Iterator[bool]:
    """Non-blocking exclusive flock for the life of the with-block. Yields False when someone else holds it."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
    held = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = True
        except OSError:
            pass
        yield held
    finally:
        os.close(fd)  # closing releases the lock; a crashed holder's lease is free by construction
