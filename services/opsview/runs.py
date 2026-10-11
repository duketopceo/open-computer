"""Run history for the console: NDJSON index plus one log file per run.

Reads never create files. `record()` is only reached from the POST /runs path.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path

MATRIX_RE = re.compile(r"^(R\d+|ROUTER|AUTH): (PASS|FAIL|PENDING) \((\d+) checks\)", re.M)
SUMMARY_RE = re.compile(r"(\d+) (passed|failed|skipped|error)", re.I)


def runs_dir() -> Path:
    return Path(os.environ.get("OC_RUNS_DIR", "/tmp/oc-audit/runs"))


def parse_matrix(out: str) -> dict[str, tuple[str, int]]:
    return {m[0]: (m[1], int(m[2])) for m in MATRIX_RE.findall(out)}


def record(out: str, *, by: str, duration_s: float, base: Path | None = None) -> dict:
    base = base or runs_dir()
    base.mkdir(parents=True, exist_ok=True)
    matrix = parse_matrix(out)
    states = [s for s, _ in matrix.values()]
    status = "failed" if "FAIL" in states or not matrix else ("pending" if "PENDING" in states else "passed")
    run_id = "run_" + uuid.uuid4().hex[:8]
    row = {
        "id": run_id, "ts": time.time(), "by": by, "status": status,
        "duration_s": round(duration_s, 1),
        "rules": {k: list(v) for k, v in matrix.items()},
        "lines": out.count("\n") + (1 if out and not out.endswith("\n") else 0),
        "summary": (out.strip().splitlines() or [""])[-1],
    }
    (base / f"{run_id}.log").write_text(out)
    with open(base / "index.ndjson", "a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def list_runs(base: Path | None = None) -> list[dict]:
    base = base or runs_dir()
    idx = base / "index.ndjson"
    if not idx.exists():
        return []
    rows = []
    for line in idx.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return sorted(rows, key=lambda r: r["ts"], reverse=True)


def get_run(run_id: str, base: Path | None = None) -> tuple[dict, str] | None:
    base = base or runs_dir()
    if not re.fullmatch(r"run_[0-9a-f]{8}", run_id):
        return None
    row = next((r for r in list_runs(base) if r["id"] == run_id), None)
    log = base / f"{run_id}.log"
    if row is None or not log.exists():
        return None
    return row, log.read_text(errors="replace")
