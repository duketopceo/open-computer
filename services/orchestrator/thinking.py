"""Per-run log of the reasoning text models returned — one NDJSON row per step (or `_plan` for the planner).

Kept beside orchestrator.ndjson rather than in it: list_runs reads the whole audit log on every poll, and reasoning
can run to many kilobytes. Text is credential-redacted and capped before it is written. A write failure is logged
and dropped — recording thoughts never changes a step's outcome.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from oc.obs import error_fields, get_logger, redact_str

CAP = 16_000
PLAN = "_plan"  # subtask id of the planner's row


def join_thoughts(parts: list[str]) -> str:
    return "\n\n".join(parts)
log = get_logger("thinking")


class ThinkingLog:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def append(self, run: str, task: str, subtask: str, text: str) -> None:
        text = redact_str(text)
        if len(text) > CAP:
            text = text[:CAP] + "\n… [truncated]"
        rec = {"run": run, "task": task, "subtask": subtask, "ts": time.time(), "text": text}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                os.write(fd, (json.dumps(rec) + "\n").encode())  # single O_APPEND write — no torn lines
            finally:
                os.close(fd)
        except OSError as e:
            log.warning("thinking not recorded", task=task, **error_fields(e))

    def rows(self) -> list[dict]:
        try:
            text = self.path.read_text()
        except FileNotFoundError:
            return []
        out = []
        for ln in text.splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if isinstance(r, dict) and isinstance(r.get("text"), str):
                out.append(r)
        return out
