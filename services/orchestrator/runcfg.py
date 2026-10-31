"""Per-run effort settings on disk: the cost ledger and the model a run was started with.

A run started with a budget has `cost.ndjson` (a `CostLedger` whose cap is a row) and an `effort_set`
audit row. Every process that drives the run (the console's thread, a restart, a durable worker) reads
them back from here, so a restart never changes what the run may spend or which model it uses.
Runs without the file (older runs, runs from other entry points) stay uncapped and use the defaults.
"""

from __future__ import annotations

from pathlib import Path

from services.cost import CostLedger

COST_FILE = "cost.ndjson"


def ledger_for(run_dir: Path | str) -> CostLedger | None:
    f = Path(run_dir) / COST_FILE
    return CostLedger(f, default_cap=0.0) if f.exists() else None


def create_ledger(run_dir: Path, run_id: str, budget: float, by: str) -> CostLedger:
    ledger = CostLedger(run_dir / COST_FILE, default_cap=budget)
    ledger.set_cap(run_id, budget, by=by)  # the cap is a row, so a restart re-reads it
    return ledger


def effort_row(rows: list[dict]) -> dict | None:
    return next((r for r in rows if r.get("event") == "effort_set"), None)


def model_of(rows: list[dict]) -> str | None:
    row = effort_row(rows)
    return row["model"] if row and row.get("model") else None
