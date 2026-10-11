"""Append-only audit log — NDJSON, one row per event.

delete() always raises ReadOnly: the log is evidence, not state.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


class ReadOnly(RuntimeError):
    pass


class AuditLog:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def append(self, row: dict) -> None:
        rec = {**row, "ts": time.time()}
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def rows(self) -> list[dict]:
        return [
            json.loads(l) for l in self.path.read_text().splitlines() if l.strip()
        ]

    def delete(self, i: int) -> None:
        raise ReadOnly("audit log is append-only")
