"""Replay: serve a recorded goal stream with no sandbox and no model calls.

A recording is `<goal>.ndjson` (events) plus `<goal>.meta.json`:
  holds    {"<seq>": "approval:<id>" | "resume"}  replay pauses before that event until a human acts
  on_deny  [[type, data], ...]                    what the run does instead if the held approval is denied
  files    {"<artifact id>": "<file>"}            bytes served at /goals/<g>/artifacts/<id>

With hold=True (default) the held event is not replayed: the human's real decision is emitted by the server
and the recording continues after it, so approvals are genuinely interactive and payload-hash bound.
With hold=False the whole recording plays as recorded, which is what the determinism gate uses.
speed=0 plays instantly; otherwise the recorded gap between events is divided by `speed`, capped at 1.5s.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from oc.events import Emitter, FileLog, MemoryLog

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MAX_GAP = 1.5


def load_recordings(directory: Path = FIXTURES) -> dict[str, dict]:
    out = {}
    for meta in sorted(directory.glob("g_*.meta.json")):
        m = json.loads(meta.read_text())
        events = [json.loads(ln) for ln in (directory / f"{m['id']}.ndjson").read_text().splitlines() if ln.strip()]
        out[m["id"]] = {"meta": m, "events": events, "dir": directory / "artifacts" / m["id"]}
    return out


class ReplaySession:
    def __init__(self, workspace, goal: str, speed: float = 4.0, hold: bool = True):
        self.ws, self.goal, self.speed, self.hold = workspace, goal, speed, hold
        rec = workspace.recordings[goal]
        self.events, self.meta = rec["events"], rec["meta"]
        self._gates: dict[str, dict | None] = {}
        self._cv = threading.Condition()
        self._stop = False
        self.cap_override: float | None = None
        self.shift = 0.0  # recorded ts -> emitted ts, moved by the human's decision time
        self.state = "playing"
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"replay-{goal}")

    def start(self):
        self.thread.start()

    def stop(self):
        with self._cv:
            self._stop = True
            self._cv.notify_all()

    def waiting_for(self) -> str | None:
        with self._cv:
            return next((g for g, v in self._gates.items() if v is None), None)

    def release(self, gate: str, info: dict | None = None) -> bool:
        with self._cv:
            if gate in self._gates and self._gates[gate] is None:
                self._gates[gate] = info or {}
                self._cv.notify_all()
                return True
            return False

    def _wait_gate(self, gate: str) -> dict | None:
        with self._cv:
            self._gates[gate] = None
            self.state = "held"
            while self._gates[gate] is None and not self._stop:
                self._cv.wait(0.25)
            self.state = "playing"
            return self._gates[gate]

    def _last_ts(self) -> float:
        evs = self.ws.log.read(self.goal)
        return evs[-1]["ts"] if evs else 0.0

    def _run(self):
        log = self.ws.log
        prev_rec = None
        for ev in self.events:
            if self._stop:
                break
            gate = self.meta["holds"].get(str(ev["seq"])) if self.hold else None
            if gate:
                info = self._wait_gate(gate)
                if self._stop:
                    break
                prev_rec = ev["ts"]
                self.shift = self._last_ts() - ev["ts"]
                if info and info.get("deny"):
                    self._deny()
                    break
                if info and info.get("cap_usd"):
                    self.cap_override = float(info["cap_usd"])
                continue  # the human's real decision stands in for the recorded event
            if prev_rec is not None and self.speed:
                time.sleep(min(max(ev["ts"] - prev_rec, 0.0) / self.speed, MAX_GAP))
            data = ev["data"]
            if self.cap_override and ev["type"] == "cost":
                data = {**data, "cap_usd": self.cap_override}
            ev_out = log.append(self.goal, ev["type"], data, ev["ts"] + self.shift, ev.get("actor"))
            if ev["type"] == "approval" and data["status"] == "pending":
                self.ws.register_approval(self.goal, ev_out)
            prev_rec = ev["ts"]
        self.state = "complete" if not self._stop else "stopped"

    def _deny(self):
        em = Emitter(self.ws.log, self.goal, actor=self.events[0].get("actor"), clock=lambda: self._last_ts() + 0.5)
        for type_, data in self.meta["on_deny"]:
            em.emit(type_, data)


class Workspace:
    """Holds the log, the per-goal approval queues and (in replay mode) the recordings."""

    def __init__(self, *, replay: bool, state_dir: Path, events_dir: Path | None = None):
        self.replay = replay
        self.state_dir = Path(state_dir)
        self.recordings = load_recordings() if replay else {}
        self.log = MemoryLog() if replay else FileLog(events_dir or self.state_dir / "events")
        self.sessions: dict[str, ReplaySession] = {}
        self._lock = threading.Lock()

    # -- goals -----------------------------------------------------------------
    def goal_ids(self) -> list[str]:
        return sorted(set(self.recordings) | set(self.log.goals()))

    def known(self, goal: str) -> bool:
        return goal in self.recordings or goal in self.log.goals()

    def _gate(self, goal: str):
        """Decisions land in an audit log. Replay runs restart their world, so they audit to a
        per-goal file that `start_replay` wipes alongside the queue — a re-run must not trip the
        gate's single-use check on the previous run's grants."""
        from services.gate.gate import Gate
        if self.replay:
            return Gate(self.state_dir / f"{goal}.gate-audit.ndjson")
        from services.opsview import server
        return Gate(server.audit_path())

    def queue(self, goal: str, *, write: bool = False):
        from services.gate.queue import ApprovalQueue

        path = self.state_dir / f"{goal}.approvals.ndjson"
        if write:
            return ApprovalQueue(path, self._gate(goal))
        return ApprovalQueue(path)

    def register_approval(self, goal: str, ev: dict):
        from services.gate.queue import ApprovalQueue
        d = ev["data"]
        q = ApprovalQueue(self.state_dir / f"{goal}.approvals.ndjson", self._gate(goal))
        if q.get(d["id"]) is None:
            q.submit(
                who=(ev.get("actor") or {}).get("name", "agent"),
                session=(ev.get("actor") or {}).get("session", ""),
                what=d["action"],
                est_cost=float(d.get("cost_usd") or 0),
                item_id=d["id"],
                ts=ev["ts"],
                bound_hash=d["payload_hash"],
                payload=d["payload"],
            )

    # -- replay control -----------------------------------------------------------
    def start_replay(self, goal: str, *, speed: float = 4.0, hold: bool = True, restart: bool = False) -> ReplaySession:
        if not self.replay or goal not in self.recordings:
            raise KeyError(goal)
        with self._lock:
            cur = self.sessions.get(goal)
            if cur and not restart:
                return cur
            if cur:
                cur.stop()
                cur.thread.join(2)
            self.log.reset(goal)
            for f in (f"{goal}.approvals.ndjson", f"{goal}.gate-audit.ndjson"):
                q = self.state_dir / f
                if q.exists():
                    q.unlink()
            s = ReplaySession(self, goal, speed, hold)
            self.sessions[goal] = s
            s.start()
            return s

    def emitter(self, goal: str, actor: dict) -> Emitter:
        if self.replay and goal in self.sessions:
            last = lambda: (self.log.read(goal)[-1]["ts"] if self.log.last_seq(goal) else 0.0) + 1.0  # noqa: E731
            return Emitter(self.log, goal, actor=actor, clock=last)
        return Emitter(self.log, goal, actor=actor)
