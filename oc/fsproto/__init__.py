"""Filesystem sub-agent protocol — reference implementation.

Implements docs/fs-protocol-spec.md v0.1. The filesystem is the truth;
every operation is atomic (tmpfile + rename), records are sha256-verified,
leases are O_EXCL files, and tombstones are created only after results
are flushed.

    fs = adapter(tmp_path)              # real clock
    fs = adapter(tmp_path, clock=fn)    # injectable clock for tests

Contract: tests/conformance/checks/__init__.py — satisfies R4, R7.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Callable


class NotReady(KeyError):
    """collect() on a task with no tombstone — never fabricate (R7)."""


class AlreadyLeased(RuntimeError):
    """A live lease already exists for this task."""


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _meta_text(meta: dict) -> str:
    lines = ["---"]
    for k, v in meta.items():
        lines.append(f"{k}: {json.dumps(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _parse_record(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    head, _, body = raw.partition(b"---\n")
    if head:
        raise ValueError(f"{path}: no front-matter")
    fm, _, body = body.partition(b"---\n")
    meta = {}
    for line in fm.decode().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = json.loads(v.strip())
    return meta, body


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    fd = os.open(tmp, os.O_RDONLY)
    os.fsync(fd)
    os.close(fd)
    os.rename(tmp, path)


def _write_record(path: Path, meta: dict, body: bytes) -> None:
    meta = {**meta, "sha256": _sha(body)}
    _atomic_write(path, _meta_text(meta).encode() + body)


class FSAdapter:
    """Protocol operations over a <run> root. See spec §1 for layout."""

    def __init__(self, root: Path | str, clock: Callable[[], float] = time.time):
        self.root = Path(root)
        (self.root / "tasks").mkdir(parents=True, exist_ok=True)
        (self.root / "agents").mkdir(exist_ok=True)
        self._clock = clock

    # --- helpers -------------------------------------------------------------

    def _task_dir(self, tid: str) -> Path:
        return self.root / "tasks" / tid

    def _agent_dir(self, agent: str) -> Path:
        d = self.root / "agents" / agent
        (d / "inbox").mkdir(parents=True, exist_ok=True)
        (d / "outbox").mkdir(exist_ok=True)
        return d

    def _lease_file(self, tid: str) -> Path:
        return self._task_dir(tid) / "lease"

    def _lease_valid(self, tid: str) -> bool:
        lf = self._lease_file(tid)
        if not lf.exists():
            return False
        meta, _ = _parse_record(lf)
        return float(meta.get("deadline", 0)) > self._clock()

    def _tombstoned(self, tid: str) -> bool:
        return (self._task_dir(tid) / "tombstone").exists()

    def _set_status(self, tid: str, status: str) -> None:
        _atomic_write(self._task_dir(tid) / "status", status.encode())

    # --- protocol ops (spec §3–§7) -------------------------------------------

    def send(self, dst: str, msg: dict) -> str:
        """Spawn: create task envelope, offer it in dst's inbox. §3."""
        tid = uuid.uuid4().hex
        td = self._task_dir(tid)
        td.mkdir(parents=True)
        body = json.dumps(msg).encode()
        _write_record(
            td / "task.md",
            {
                "id": tid,
                "from": "orchestrator",
                "to": dst,
                "kind": "task",
                "deps": [],
                "ttl": None,
                "created": self._clock(),
            },
            body,
        )
        self._set_status(tid, "queued")
        inbox = self._agent_dir(dst) / "inbox"
        _atomic_write(inbox / tid, tid.encode())
        return tid

    def outbox_write(self, agent: str, tid: str, payload) -> None:
        """Complete: write result, fsync, tombstone LAST. §3/§5."""
        body = payload if isinstance(payload, bytes) else str(payload).encode()
        td = self._task_dir(tid)
        _write_record(
            td / "result.md",
            {
                "id": tid,
                "from": agent,
                "to": "parent",
                "kind": "result",
                "created": self._clock(),
            },
            body,
        )
        (self._agent_dir(agent) / "outbox" / tid).write_bytes(tid.encode())
        _atomic_write(td / "tombstone", b"")
        self._set_status(tid, "done")

    def collect(self, parent: str, tid: str):
        """Read result iff tombstoned; byte-exact body. §5."""
        td = self._task_dir(tid)
        if not self._tombstoned(tid):
            raise NotReady(tid)
        rec = td / "result.md"
        if not self.verify(rec):
            raise ValueError(f"{rec}: integrity check failed")
        meta, body = _parse_record(rec)
        return body.decode()

    def enqueue(self, consumer: str, depends_on: str) -> None:
        """Register a dependency edge; consumer waits for tombstone. §5."""
        deps = self._agent_dir(consumer) / "deps"
        existing = deps.read_text().splitlines() if deps.exists() else []
        _atomic_write(deps, ("\n".join([*existing, depends_on]) + "\n").encode())

    def ready(self, consumer: str) -> bool:
        """True iff every dep is tombstoned. §5."""
        deps = self._agent_dir(consumer) / "deps"
        if not deps.exists():
            return True
        wanted = [l for l in deps.read_text().splitlines() if l.strip()]
        return all(self._tombstoned(t) for t in wanted)

    def claim(self, agent: str, tid: str, ttl: float) -> str:
        """Atomic O_EXCL lease; expired leases are reclaimed. §4."""
        lf = self._lease_file(tid)
        meta = {"holder": agent, "task": tid, "deadline": self._clock() + ttl}
        for _ in range(2):
            try:
                fd = os.open(lf, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    valid = self._lease_valid(tid)
                except (ValueError, OSError, json.JSONDecodeError):
                    valid = True  # mid-write lease = live claimer; fail closed
                if valid:
                    raise AlreadyLeased(tid)
                try:
                    lf.unlink()  # expired — reclaim
                except FileNotFoundError:
                    pass  # another reclaimer beat us to it; retry the create
                continue
            with os.fdopen(fd, "wb") as f:
                f.write(_meta_text(meta).encode())
            self._set_status(tid, "leased")
            return tid
        raise AlreadyLeased(tid)

    def expire(self, lease_id: str) -> None:
        """Force lease expiry → task becomes pending again. §4."""
        lf = self._lease_file(lease_id)
        if lf.exists():
            lf.unlink()
        if not self._tombstoned(lease_id):
            self._set_status(lease_id, "queued")

    def pending(self, tid: str) -> bool:
        """Task exists, no live lease, no tombstone, not failed. §4."""
        status = self._task_dir(tid) / "status"
        if status.exists() and status.read_text().strip() == "failed":
            return False  # failed is terminal for the run — not a requeue
        return (
            self._task_dir(tid).exists()
            and not self._lease_valid(tid)
            and not self._tombstoned(tid)
        )

    def done(self, tid: str) -> bool:
        """Tombstone present → result is final and collectable. §5."""
        return self._tombstoned(tid)

    def fail(self, tid: str) -> None:
        """Mark a task failed — never tombstoned, never collectable. §5."""
        self._set_status(tid, "failed")

    def inbox_ids(self, agent: str) -> list[str]:
        """Task ids offered to an agent's inbox, in name order. §3."""
        inbox = self._agent_dir(agent) / "inbox"
        return sorted(p.name for p in inbox.iterdir())

    def task_body(self, tid: str) -> bytes:
        """Raw task.md body — what the worker executes. §3."""
        meta, body = _parse_record(self._task_dir(tid) / "task.md")
        return body

    # --- integrity (spec §6) --------------------------------------------------

    def record_path(self, agent: str, tid: str) -> str:
        return str(self._task_dir(tid) / "result.md")

    def write_raw(self, path: str, data: bytes) -> None:
        """Low-level write hook — tamper simulation; verify() must catch it."""
        Path(path).write_bytes(data)

    def verify(self, path) -> bool:
        """sha256(body) == front-matter digest. §6."""
        try:
            meta, body = _parse_record(Path(path))
        except (ValueError, OSError, json.JSONDecodeError):
            return False
        return _sha(body) == meta.get("sha256")


def adapter(root: Path | str, clock: Callable[[], float] = time.time) -> FSAdapter:
    return FSAdapter(root, clock=clock)
