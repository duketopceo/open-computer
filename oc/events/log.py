"""Event logs: where a goal's events live and how readers tail them.

`MemoryLog` is for tests and replay. `FileLog` is one NDJSON file per goal
(`<dir>/<goal>.ndjson`) or one fixed file for a single goal, append-only like the audit log. Both assign the
gapless `seq` and both wake tailing readers; `FileLog` is also safe across processes (flocked append).
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path

TERMINAL = {"done", "failed", "cancelled"}


def line(event: dict) -> str:
    return json.dumps(event, separators=(",", ":"), ensure_ascii=False)


class _Base:
    def __init__(self):
        self._cv = threading.Condition()

    # subclasses implement _load(goal) -> list[dict] and _store(goal, event)
    def append(self, goal: str, type_: str, data: dict, ts: float, actor: dict | None = None) -> dict:
        with self._cv:
            seq = self.last_seq(goal) + 1
            ev = {"id": f"{goal}:{seq}", "v": 1, "goal": goal, "seq": seq, "ts": round(float(ts), 3), "type": type_}
            if actor:
                ev["actor"] = actor
            ev["data"] = data
            self._store(goal, ev)
            self._cv.notify_all()
            return ev

    def append_raw(self, event: dict) -> dict:
        """Store an already-formed event (replay). seq must continue the goal's sequence."""
        with self._cv:
            want = self.last_seq(event["goal"]) + 1
            if event["seq"] != want:
                raise ValueError(f"seq {event['seq']} out of order, expected {want}")
            self._store(event["goal"], event)
            self._cv.notify_all()
            return event

    def read(self, goal: str, after: int = 0) -> list[dict]:
        with self._cv:
            return [e for e in self._load(goal) if e["seq"] > after]

    def last_seq(self, goal: str) -> int:
        evs = self._load(goal)
        return evs[-1]["seq"] if evs else 0

    def wait(self, goal: str, after: int, timeout: float) -> bool:
        """Block until the goal has an event past `after` or the timeout passes."""
        with self._cv:
            if self.last_seq(goal) > after:
                return True
            self._cv.wait(timeout)
            return self.last_seq(goal) > after

    def finished(self, goal: str) -> bool:
        runs = [e for e in self._load(goal) if e["type"] == "run"]
        return bool(runs) and runs[-1]["data"]["status"] in TERMINAL

    def goals(self) -> list[str]:
        raise NotImplementedError


class MemoryLog(_Base):
    def __init__(self):
        super().__init__()
        self._d: dict[str, list[dict]] = {}

    def _load(self, goal):
        return self._d.get(goal, [])

    def _store(self, goal, ev):
        self._d.setdefault(goal, []).append(ev)

    def goals(self):
        return sorted(self._d)

    def reset(self, goal: str) -> None:
        with self._cv:
            self._d.pop(goal, None)
            self._cv.notify_all()


_CHUNK = 8192


def _complete_lines_reversed(f, size: int):
    """Yield the newline-terminated lines of an open binary file, last first, reading backwards in chunks.

    Bytes after the final newline are a torn write and are skipped, so a crash mid-append never hides the
    event before it."""
    tail, pos, started = b"", size, False
    while pos > 0:
        step = min(_CHUNK, pos)
        pos -= step
        f.seek(pos)
        tail = f.read(step) + tail
        if not started:
            cut = tail.rfind(b"\n")
            if cut < 0:
                continue
            tail, started = tail[: cut + 1], True
        while True:
            prev = tail.rfind(b"\n", 0, len(tail) - 1)
            if prev < 0:
                break
            yield tail[prev + 1 : -1]
            tail = tail[: prev + 1]
    if started and tail:
        yield tail[:-1]


def last_seq_in(path: Path) -> int:
    """Seq of the last complete, parseable event in an NDJSON event file. Reads only the end of the file."""
    try:
        f = open(path, "rb")
    except FileNotFoundError:
        return 0
    with f:
        for raw in _complete_lines_reversed(f, os.fstat(f.fileno()).st_size):
            try:
                return int(json.loads(raw)["seq"])
            except (ValueError, KeyError, TypeError):
                continue  # a corrupt line is not an event: the one before it decides the next seq
    return 0


class FileLog(_Base):
    """NDJSON event log. One file per goal (`<dir>/<goal>.ndjson`), or, with `filename`, one fixed file
    (`<dir>/<filename>`) for a single goal whose directory is its own (the dev trace stream).

    `append` takes an exclusive `flock` on a sibling `<file>.lock`, reads only the last complete line to find the
    next seq, and writes the event with one `O_APPEND` write, so several processes can feed one stream without
    gaps or duplicates and an append never re-parses the file. Readers (`read`, `last_seq`, `finished`) take no
    lock and never create the lock file; a torn trailing line is not an event yet."""

    def __init__(self, directory: Path | str, filename: str | None = None):
        super().__init__()
        self.dir = Path(directory)
        self.filename = filename
        self._cache: dict[str, tuple[int, list[dict]]] = {}

    def _path(self, goal: str) -> Path:
        if "/" in goal or ".." in goal:
            raise ValueError("bad goal id")
        return self.dir / (self.filename or f"{goal}.ndjson")

    @contextmanager
    def _flock(self, goal: str):
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(str(self._path(goal)) + ".lock", "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def append(self, goal, type_, data, ts, actor=None):
        with self._flock(goal):
            return super().append(goal, type_, data, ts, actor)

    def append_raw(self, event):
        with self._flock(event["goal"]):
            return super().append_raw(event)

    def last_seq(self, goal: str) -> int:
        return last_seq_in(self._path(goal))

    def _load(self, goal):
        p = self._path(goal)
        try:
            size = p.stat().st_size
        except FileNotFoundError:
            return []
        # Re-read only when the file grew: the cache is a pure function of the file.
        cached = self._cache.get(goal)
        if cached is not None and cached[0] == size:
            return cached[1]
        out = []
        for raw in p.read_bytes()[:size].decode("utf-8", "replace").splitlines():
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue  # a torn trailing line is not an event yet
        self._cache[goal] = (size, out)
        return out

    def _store(self, goal, ev):
        payload = (line(ev) + "\n").encode()
        fd = os.open(self._path(goal), os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            size = os.fstat(fd).st_size
            if size and os.pread(fd, 1, size - 1) != b"\n":
                payload = b"\n" + payload  # a crash left a torn line: keep it a separate (unreadable) line
            os.write(fd, payload)
        finally:
            os.close(fd)
        self._cache.pop(goal, None)

    def goals(self):
        return sorted(p.stem for p in self.dir.glob("g_*.ndjson")) if self.dir.exists() else []
