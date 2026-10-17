"""Budget gate — wisp's pattern, ported flat.

Per-task and per-day caps on decision spend. Exceeding a cap halts routing
(fail closed — BudgetHalt), and every spend/decision lands in the ledger.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class BudgetHalt(Exception):
    pass


class Budget:
    def __init__(self, ledger_path: Path | str, daily_cap: float = 1.00, task_cap: float = 0.10):
        self.path = Path(ledger_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.daily_cap = daily_cap
        self.task_cap = task_cap
        self.task_spend = 0.0

    def _append(self, row: dict) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps({**row, "ts": time.time()}) + "\n")

    def day_spend(self) -> float:
        if not self.path.exists():
            return 0.0
        today = time.strftime("%Y-%m-%d")
        total = 0.0
        for line in self.path.read_text().splitlines():
            try:
                row = json.loads(line)
                cost = float(row.get("cost", 0.0))
            except (json.JSONDecodeError, ValueError, TypeError):
                self._append({"event": "budget_halt", "cap": "ledger-corrupt"})
                raise BudgetHalt("spend ledger unreadable — failing closed")
            if time.strftime("%Y-%m-%d", time.localtime(row.get("ts", 0))) == today:
                total += cost
        return total

    def check(self, est_cost: float = 0.0) -> None:
        """Raise BudgetHalt if a call of est_cost would exceed any cap."""
        if self.task_spend + est_cost > self.task_cap:
            self._append({"event": "budget_halt", "cap": "task", "spend": self.task_spend})
            raise BudgetHalt(f"task cap {self.task_cap} exceeded")
        if self.day_spend() + est_cost > self.daily_cap:
            self._append({"event": "budget_halt", "cap": "daily"})
            raise BudgetHalt(f"daily cap {self.daily_cap} exceeded")

    def record(self, cost: float, engine: str, route: str) -> None:
        self.task_spend += cost
        self._append({"event": "decision", "engine": engine, "route": route, "cost": cost})
