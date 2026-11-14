"""Per-run sidecar of what the run resolved — one NDJSON row per run.

Same shape and the same failure rule as `thinking.py`: one `O_APPEND` write per row so concurrent appends cannot
tear a line, and a write failure is logged and dropped because recording a resolution never changes whether the
run dispatches. Kept beside `orchestrator.ndjson` rather than in it, since `list_runs` reads the whole audit log
on every poll and a resolution row carries a skill list.

Why not `<root>/workspaces/<job>`: that tree is the seeded file tree a sandbox boots from
(`services/agents/workspace.py`), not the run record store, and writing records into it would ship them to the
model.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from oc.obs import error_fields, get_logger

log = get_logger("orchestrator")


class SkillsLog:
    """Append-only `skills.ndjson`: `{run, ts, ...}` per run."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def append(self, run: str, record: dict) -> None:
        rec = {"run": run, "ts": time.time(), **record}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                os.write(fd, (json.dumps(rec) + "\n").encode())
            finally:
                os.close(fd)
        except OSError as e:
            log.warning("skills not recorded", run=run, **error_fields(e))

    def rows(self) -> list[dict]:
        try:
            text = self.path.read_text()
        except (FileNotFoundError, OSError):
            return []
        out = []
        for ln in text.splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if isinstance(r, dict) and isinstance(r.get("run"), str):
                out.append(r)
        return out

    def for_run(self, run: str) -> dict | None:
        """The last resolution recorded for `run`, or None."""
        return next((r for r in reversed(self.rows()) if r.get("run") == run), None)


__all__ = ["SkillsLog"]
