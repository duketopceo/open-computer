"""Worker cost ledger: every model call a run makes, per subtask, with a hard cap that PAUSES.

Cap semantics (the product's headline promise):
  * `check(run, est_cost)` before each unit of paid work: if spend + est would exceed the cap the run goes
    to state `paused_cap`. Paused means: nothing new is started. Work already in flight finishes and is
    recorded. Nothing is cancelled, failed or deleted, so no work is lost and the owner can resume.
  * `check` RESERVES the estimate and returns a token; `record(..., token=)` or `release(token)` settles it.
    Concurrent workers therefore cannot all pass the check on the same headroom. The run total never exceeds
    the cap by more than the amount by which a call's real cost beat its own estimate. A worker that dies
    without settling leaves its reservation held (fail closed); resuming with a higher cap clears the stall.
  * `resume(run, new_cap, by)` is the only way out of `paused_cap`, and must raise the cap above current spend.
  * The ledger is an append-only NDJSON file; cap, pauses and resumes are rows, so state survives restarts.
    An unreadable row fails closed: the run is treated as paused (never as free to spend).
  * Cost comes from the provider's response, not a local price table (prices drift).

Events: pass `emit=callable(dict)` to stream `cost`, `run_paused`, `run_resumed` dicts. The event schema is
owned by the UI event-stream contract (`docs/design/event-stream.md` on the workspace branch); this module
only hands plain dicts to whatever emitter is wired in, and an emitter failure never affects accounting.
"""

from __future__ import annotations

import fcntl
import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from oc.obs import get_logger

log = get_logger("cost")

RUNNING = "running"
PAUSED = "paused_cap"


class LedgerCorrupt(RuntimeError):
    pass


@dataclass(frozen=True)
class Usage:
    model: str
    tokens_in: int
    tokens_out: int
    cost: float  # dollars, as reported by the provider


@dataclass
class RunState:
    run: str
    cap: float | None
    spend: float
    state: str
    calls: int
    reserved: float = 0.0


class CostLedger:
    def __init__(
        self,
        path: Path | str,
        default_cap: float = 0.10,
        *,
        emit: Callable[[dict], None] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.default_cap = default_cap
        self._emit = emit
        self._clock = clock

    # --- reads -------------------------------------------------------------
    def _rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("row is not an object")
            except ValueError as e:
                raise LedgerCorrupt(f"{self.path}: unreadable row: {e}") from e
            rows.append(row)
        return rows

    def state(self, run: str) -> RunState:
        cap, spend, calls, state = self.default_cap, 0.0, 0, RUNNING
        held: dict[str, float] = {}
        try:
            rows = self._rows()
        except LedgerCorrupt as e:
            log.error("ledger unreadable: run treated as paused", run_id=run, error=str(e))
            return RunState(run, None, 0.0, PAUSED, 0)  # fail closed: unreadable means paused
        for r in rows:
            if r.get("run") != run:
                continue
            ev = r.get("event")
            if ev == "cost":
                spend += float(r["cost"])
                calls += 1
            elif ev == "reserve":
                held[r["token"]] = float(r["est"])
            elif ev == "release":
                held.pop(r["token"], None)
            elif ev == "cap_set":
                cap = float(r["cap"])
            elif ev == "run_paused":
                state = PAUSED
            elif ev == "run_resumed":
                state = RUNNING
        return RunState(run, cap, spend, state, calls, sum(held.values()))

    def total(self, run: str) -> float:
        return self.state(run).spend

    def by_subtask(self, run: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for r in self._rows():
            if r.get("run") == run and r.get("event") == "cost":
                out[r["subtask"]] = out.get(r["subtask"], 0.0) + float(r["cost"])
        return out

    # --- writes ------------------------------------------------------------
    @contextmanager
    def _locked(self) -> Iterator[None]:
        with open(str(self.path) + ".lock", "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def _append(self, row: dict) -> dict:
        rec = {**row, "ts": self._clock()}
        with open(self.path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        return rec

    def _notify(self, rec: dict) -> None:
        if self._emit is not None:
            try:
                self._emit(rec)
            except Exception as e:  # an emitter must never change accounting
                log.warning("cost event emitter failed", error=type(e).__name__)

    def set_cap(self, run: str, cap: float, by: str = "owner") -> None:
        with self._locked():
            self._notify(self._append({"event": "cap_set", "run": run, "cap": float(cap), "by": by}))
            log.info("cap set", run_id=run, cap=float(cap), by=by)

    def check(self, run: str, est_cost: float = 0.0) -> str | None:
        """Reserve `est_cost` under the cap. Returns a token, or None if it does not fit (run is paused,
        recorded once). Settle the token with record(token=) or release()."""
        with self._locked():
            st = self.state(run)
            if st.state == PAUSED:
                log.info("reserve refused: run paused", run_id=run, cap=st.cap, spend=st.spend)
                return None
            if st.cap is None or st.spend + st.reserved + est_cost > st.cap:
                log.warning("run paused: cap", run_id=run, cap=st.cap, spend=st.spend, reserved=st.reserved,
                            est_cost=est_cost)
                self._notify(
                    self._append(
                        {
                            "event": "run_paused",
                            "run": run,
                            "reason": "cap",
                            "cap": st.cap,
                            "spend": st.spend,
                            "est_cost": est_cost,
                        }
                    )
                )
                return None
            token = uuid.uuid4().hex[:10]
            self._append({"event": "reserve", "run": run, "token": token, "est": est_cost})
            log.debug("reserved", run_id=run, reservation=token, est_cost=est_cost, cap=st.cap, spend=st.spend)
            return token

    def release(self, run: str, token: str) -> None:
        """Settle a reservation that produced no cost (worker failed before spending)."""
        with self._locked():
            self._append({"event": "release", "run": run, "token": token})
            log.debug("released", run_id=run, reservation=token)

    def record(self, run: str, subtask: str, usage: Usage, token: str | None = None) -> RunState:
        """Record a finished call. Always recorded, even over the cap: money already spent is truth."""
        with self._locked():
            self._notify(
                self._append(
                    {
                        "event": "cost",
                        "run": run,
                        "subtask": subtask,
                        "model": usage.model,
                        "tokens_in": usage.tokens_in,
                        "tokens_out": usage.tokens_out,
                        "cost": usage.cost,
                    }
                )
            )
            if token:
                self._append({"event": "release", "run": run, "token": token})
            st = self.state(run)
            log.info("cost recorded", run_id=run, subtask=subtask, model=usage.model, tokens_in=usage.tokens_in,
                     tokens_out=usage.tokens_out, cost=usage.cost, spend=st.spend, cap=st.cap)
            if st.cap is not None and st.state == RUNNING and st.spend + st.reserved >= st.cap:
                self._notify(
                    self._append(
                        {
                            "event": "run_paused",
                            "run": run,
                            "reason": "cap",
                            "cap": st.cap,
                            "spend": st.spend,
                        }
                    )
                )
                st.state = PAUSED
                log.warning("run paused: cap", run_id=run, cap=st.cap, spend=st.spend)
            return st

    def resume(self, run: str, new_cap: float, by: str) -> RunState:
        """Owner action: raise the cap above current spend and continue. Refuses otherwise."""
        with self._locked():
            st = self.state(run)
            if new_cap <= st.spend:
                raise ValueError(f"new cap {new_cap} must exceed current spend {st.spend:.6f}")
            self._notify(self._append({"event": "cap_set", "run": run, "cap": float(new_cap), "by": by}))
            self._notify(self._append({"event": "run_resumed", "run": run, "by": by, "cap": float(new_cap)}))
            log.info("run resumed", run_id=run, cap=float(new_cap), spend=st.spend, by=by)
            return self.state(run)
