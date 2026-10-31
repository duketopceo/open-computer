"""Replay of one job's trail rows into the state the planner loop needs to continue.

The trail is the job's state (it is append-only and written before the owner or a worker is waited on), so a
restarted process rebuilds everything from it. Pure: rows in, `ReplayState` out; no I/O, no clock.

`warned` and granted egress hosts come only from APPROVED `approval` rows. An `approval_request` or
`budget_warn` row is written before the owner answers, so it proves nothing about a decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .worker import money

HISTORY_TAIL = 8
RESULT_CAP = 2000  # shared with the loop: replayed history must match what the live loop wrote
DEFAULT_EST = 1.0  # the loop's estimate when a spawn row (older trails) carries none
INTERRUPTED_RESULT = "interrupted: the process stopped mid-round; charged its estimate"


@dataclass
class ReplayState:
    started: bool = False
    goal: str = ""
    budget: float | None = None
    model: str = ""
    plan_steps: list | None = None  # None: no `plan` row yet
    plan_approved: bool | None = None  # None: unresolved; only the approval row after the plan row counts
    spent: float = 0.0
    failures: int = 0
    warned: bool = False
    granted: set[str] = field(default_factory=set)
    history: list[dict] = field(default_factory=list)  # last HISTORY_TAIL entries
    last_round: int = 0  # last round that finished (result) or was interrupted
    inflight: dict | None = None  # {"round", "title", "est"}: spawned, no result, not yet marked interrupted
    outcome: dict | None = None  # the `job_end` row, when the job ended


def replay(rows: list[dict]) -> ReplayState:
    st = ReplayState()
    meta = None  # job_submit wins over job_start: it holds the full goal; job_start clips it
    pending: dict[str, dict] = {}  # kind -> detail of the latest approval_request
    rounds: dict[int, dict] = {}
    history: list[dict] = []
    for r in rows:
        ev = r.get("event")
        if ev == "job_submit":
            meta = r
            st.started = True
        elif ev == "job_start":
            st.started = True
            meta = meta if meta is not None and meta.get("event") == "job_submit" else r
        elif ev == "plan":
            st.plan_steps = list(r.get("steps") or [])
            st.plan_approved = None  # a new plan row opens a new approval
        elif ev == "approval_request":
            pending[str(r.get("kind"))] = r.get("detail") or {}
        elif ev == "approval":
            kind, ok = str(r.get("kind")), bool(r.get("approved"))
            detail = pending.pop(kind, {})
            if kind == "plan":
                st.plan_approved = ok
            elif ok and kind == "spend":
                st.warned = True
            elif ok and kind == "egress" and detail.get("host"):
                st.granted.add(str(detail["host"]))
        elif ev == "spawn":
            n = int(r.get("round") or 0)
            est = money(r.get("est"))
            rounds[n] = {"title": r.get("title"), "est": est if est else DEFAULT_EST, "done": False}
        elif ev == "result":
            n = int(r.get("round") or 0)
            cost = money(r.get("cost"), 0.0)
            st.spent += cost
            st.failures = 0 if r.get("status") == "done" else st.failures + 1
            history.append({"round": n, "title": (rounds.get(n) or {}).get("title"), "status": r.get("status"),
                            "result": str(r.get("text") or r.get("detail") or "")[:RESULT_CAP]})
            rounds.setdefault(n, {})["done"] = True
            st.last_round = max(st.last_round, n)
        elif ev == "round_interrupted":
            n = int(r.get("round") or 0)
            st.spent += money(r.get("charged"), 0.0)
            st.failures += 1
            history.append({"round": n, "title": (rounds.get(n) or {}).get("title"), "status": "failed",
                            "result": INTERRUPTED_RESULT})
            rounds.setdefault(n, {})["done"] = True
            st.last_round = max(st.last_round, n)
        elif ev == "job_end":
            st.outcome = r
    if meta is not None:
        st.goal = str(meta.get("goal") or "")
        st.budget = money(meta.get("budget"))
        st.model = str(meta.get("model") or "")
    st.history = history[-HISTORY_TAIL:]
    open_rounds = [n for n, d in rounds.items() if not d.get("done") and "est" in d]
    if open_rounds:
        n = max(open_rounds)
        st.inflight = {"round": n, "title": rounds[n].get("title"), "est": rounds[n]["est"]}
    return st
