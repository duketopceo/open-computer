"""Event logs: where a goal's events live and how readers tail them.

`MemoryLog` is for tests and replay. `FileLog` is one NDJSON file per goal
(`<dir>/<goal>.ndjson`), append-only like the audit log. Both assign the
gapless `seq`, both wake tailing readers, and both are safe across threads.
"""

from __future__ import annotations

import json
import os
import threading
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


class FileLog(_Base):
    def __init__(self, directory: Path | str):
        super().__init__()
        self.dir = Path(directory)
        self._cache: dict[str, list[dict]] = {}

    def _path(self, goal: str) -> Path:
        if "/" in goal or ".." in goal:
            raise ValueError("bad goal id")
        return self.dir / f"{goal}.ndjson"

    def _load(self, goal):
        p = self._path(goal)
        if not p.exists():
            return []
        # Re-read only when the file grew: the cache is a pure function of the file.
        size = p.stat().st_size
        cached = self._cache.get(goal)
        if cached is not None and cached[0] == size:
            return cached[1]
        out = []
        for raw in p.read_text().splitlines():
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue  # a torn trailing line is not an event yet
        self._cache[goal] = (size, out)
        return out

    def _store(self, goal, ev):
        self.dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path(goal), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(line(ev) + "\n")
        self._cache.pop(goal, None)

    def goals(self):
        return sorted(p.stem for p in self.dir.glob("g_*.ndjson")) if self.dir.exists() else []
