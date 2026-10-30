"""Append-only job trail: the record the console reads. One NDJSON file per orchestrator root.

Every row carries job, seq, ts and event; fields are redacted and size-capped on write.
"""

from __future__ import annotations

import fcntl
import json
import time
from pathlib import Path

from oc.obs import redact

_CLIP = 4000  # per string: a tool call's raw input can carry whole file bodies


def _clip(v):
    if isinstance(v, str):
        return v if len(v) <= _CLIP else v[:_CLIP] + "… [truncated]"
    if isinstance(v, dict):
        return {k: _clip(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clip(x) for x in v]
    return v


class Trail:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, job: str, event: str, **fields) -> dict:
        with open(self.path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            seq = sum(1 for ln in f if f'"job": {json.dumps(job)}' in ln) + 1
            # The console serves this file: credentials a worker's tool input or output carried never reach it.
            row = {"job": job, "seq": seq, "ts": time.time(), "event": event, **redact(_clip(fields))}
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
