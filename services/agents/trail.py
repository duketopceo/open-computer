"""Append-only job trail: the record the console reads. One NDJSON file per orchestrator root.

Every row carries job, seq, ts and event; secrets never go in (callers pass summaries, not raw env).
"""

from __future__ import annotations

import fcntl
import json
import time
from pathlib import Path


class Trail:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, job: str, event: str, **fields) -> dict:
        with open(self.path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            seq = sum(1 for ln in f if f'"job": {json.dumps(job)}' in ln) + 1
            row = {"job": job, "seq": seq, "ts": time.time(), "event": event, **fields}
            f.seek(0, 2)
            if f.tell():
                f.seek(f.tell() - 1)
                if f.read(1) != "\n":  # a torn trailing line: start the new row on its own line
                    f.write("\n")
            f.write(json.dumps(row) + "\n")
            f.flush()
        return row

    def rows(self, job: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for ln in self.path.read_text().splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue  # a torn trailing line must not hide the rest
            if job is None or r.get("job") == job:
                out.append(r)
        return out

    def jobs(self) -> list[str]:
        seen: dict[str, None] = {}
        for r in self.rows():
            seen.setdefault(r["job"], None)
        return list(seen)
