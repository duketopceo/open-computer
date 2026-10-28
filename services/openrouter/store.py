"""Local record of every OpenRouter request: append-only NDJSON, attributable, redacted, queryable offline.

One `call` row per request (success or failure). Reconciliation against OpenRouter's generation-stats API
appends a `reconcile` row; nothing is rewritten. Readers tolerate a torn last line.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from oc.obs.redact import redact


def obs_dir() -> Path:
    return Path(os.environ.get("OC_OBS_DIR") or (Path.home() / ".local/share/oc/obs"))


GROUPS = ("chat", "run", "subtask", "model", "provider", "kind", "day", "status")
_GROUP_FIELD = {"chat": "chat_id", "run": "run_id", "subtask": "subtask", "model": "model_served",
                "provider": "provider", "kind": "kind", "status": "status"}


class CallStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else obs_dir() / "openrouter-calls.ndjson"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(self.path) + ".lock", "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def append(self, row: dict) -> dict:
        rec = redact({"ts": time.time(), **row})
        with self._locked(), open(self.path, "a") as f:
            f.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")
        return rec

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue  # torn or foreign line: skip, never raise
            if isinstance(r, dict):
                out.append(r)
        return out

    def calls(self) -> list[dict]:
        """Call rows with their latest reconcile result folded in as `reconcile_status` / `reconcile`."""
        rec: dict[str, dict] = {}
        calls = []
        for r in self.rows():
            if r.get("event") == "reconcile" and r.get("call_id"):
                rec[r["call_id"]] = r
            elif r.get("event") == "call":
                calls.append(r)
        for c in calls:
            r = rec.get(c.get("call_id", ""))
            c["reconcile_status"] = r["status"] if r else "unreconciled"
            if r:
                c["reconcile"] = {k: v for k, v in r.items() if k not in ("event", "call_id")}
        return calls


def _pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(round(p * (len(vals) - 1))))]


def summarize(calls: list[dict], group_by: str = "chat", *, run: str | None = None, chat: str | None = None,
              since: float | None = None) -> list[dict]:
    """Aggregate call rows. Sorted by cost, descending."""
    if group_by not in GROUPS:
        raise ValueError(f"group_by must be one of {GROUPS}")
    groups: dict[str, dict] = {}
    for c in calls:
        if run and c.get("run_id") != run or chat and c.get("chat_id") != chat or since and c.get("ts", 0) < since:
            continue
        if group_by == "day":
            key = time.strftime("%Y-%m-%d", time.gmtime(c.get("ts", 0)))
        else:
            key = str(c.get(_GROUP_FIELD[group_by]) or "-")
        g = groups.setdefault(key, {"key": key, "calls": 0, "errors": 0, "cost": 0.0, "tokens_in": 0,
                                    "tokens_out": 0, "no_cost_reported": 0, "mismatches": 0, "_lat": []})
        g["calls"] += 1
        g["errors"] += c.get("status") != "ok"
        g["cost"] += float(c.get("cost") or 0.0)
        g["tokens_in"] += int(c.get("tokens_in") or 0)
        g["tokens_out"] += int(c.get("tokens_out") or 0)
        g["no_cost_reported"] += c.get("status") == "ok" and c.get("cost_source") != "provider"
        g["mismatches"] += c.get("reconcile_status") == "mismatch"
        if c.get("latency_ms") is not None:
            g["_lat"].append(float(c["latency_ms"]))
    out = []
    for g in groups.values():
        lat = g.pop("_lat")
        g["latency_ms_p50"], g["latency_ms_p95"] = _pct(lat, 0.5), _pct(lat, 0.95)
        g["cost"] = round(g["cost"], 8)
        out.append(g)
    return sorted(out, key=lambda g: g["cost"], reverse=True)
