"""Emitter: the one way the orchestrator writes a goal's events.

    em = Emitter(FileLog(dir), "g_research01", actor={"name": "researcher", "session": "sess-4f21"})
    em.run("running", title="Compare two vector databases", cap_usd=2.0)
    em.step("step_plan", "Plan the research", "done")
    em.source("src_pg", n=1, url="https://...", title="...", fetched=True)
    em.approval_request("ap_push01", kind="push", action="git push", payload={...}, risk="high")

Every call validates against schema/event.schema.json before anything is written,
assigns the next seq under the log's lock, and enforces the rules the UI relies on:

  * an approval id is single-use: once resolved it cannot change state again;
  * re-requesting an approval id with a different payload raises, because any change
    to the payload is a new request (new id, `supersedes` the old one);
  * `payload_hash` is always computed here, never supplied, so it cannot disagree
    with the payload.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable

from .log import _Base
from .validate import validate

DEFAULT_COST_CAP_THRESHOLDS = (0.8, 1.0)


def payload_hash(payload) -> str:
    """Same canonicalisation as services/gate/queue.py payload_hash, so the two agree."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class ApprovalStateError(Exception):
    pass


class Emitter:
    def __init__(self, log: _Base, goal: str, *, actor: dict | None = None,
                 clock: Callable[[], float] = time.time, approvals=None):
        self.log, self.goal, self.actor, self.clock = log, goal, actor, clock
        self.approvals = approvals  # optional services.gate.queue.ApprovalQueue to register requests with
        self._fired: set[float] = set()

    # -- generic ------------------------------------------------------------
    def emit(self, type_: str, data: dict, *, actor: dict | None = None) -> dict:
        ev = {"id": f"{self.goal}:0", "v": 1, "goal": self.goal, "seq": 1, "ts": 0, "type": type_, "data": data}
        if actor or self.actor:
            ev["actor"] = actor or self.actor
        validate(ev)  # fail before writing; seq/ts are placeholders here
        return self.log.append(self.goal, type_, data, self.clock(), actor or self.actor)

    @staticmethod
    def _clean(**kw) -> dict:
        return {k: v for k, v in kw.items() if v is not None}

    # -- typed helpers ------------------------------------------------------
    def run(self, status: str, **data) -> dict:
        return self.emit("run", self._clean(status=status, **data))

    def step(self, id: str, title: str, status: str, **data) -> dict:
        return self.emit("step", self._clean(id=id, title=title, status=status, **data))

    def tool_call(self, id: str, name: str, status: str, **data) -> dict:
        return self.emit("tool_call", self._clean(id=id, name=name, status=status, **data))

    def source(self, id: str, n: int, url: str, title: str, **data) -> dict:
        return self.emit("source", self._clean(id=id, n=n, url=url, title=title, **data))

    def artifact(self, id: str, name: str, kind: str, version: int, url: str, **data) -> dict:
        return self.emit("artifact", self._clean(id=id, name=name, kind=kind, version=version, url=url, **data))

    def change(self, id: str, path: str, op: str, additions: int, deletions: int, **data) -> dict:
        return self.emit("change", self._clean(id=id, path=path, op=op, additions=additions, deletions=deletions, **data))

    def note(self, id: str, role: str, text: str, **data) -> dict:
        return self.emit("note", self._clean(id=id, role=role, text=text, **data))

    def error(self, id: str, code: str, message: str, **data) -> dict:
        return self.emit("error", self._clean(id=id, code=code, message=message, **data))

    def cost(self, spent_usd: float, cap_usd: float, **data) -> dict:
        """Emit a cumulative cost snapshot. `threshold` is set automatically the first time
        spend crosses 80% and 100% of the cap, so the UI can post the inline event."""
        crossed = [t for t in DEFAULT_COST_CAP_THRESHOLDS
                   if cap_usd > 0 and spent_usd >= t * cap_usd and t not in self._fired]
        if crossed and "threshold" not in data:
            data["threshold"] = max(crossed)
            self._fired.update(crossed)
        return self.emit("cost", self._clean(spent_usd=round(spent_usd, 6), cap_usd=cap_usd, **data))

    # -- approvals ------------------------------------------------------------
    def _approval_state(self, id: str) -> dict | None:
        state = None
        for e in self.log.read(self.goal):
            if e["type"] == "approval" and e["data"]["id"] == id:
                state = {**(state or {}), **e["data"]}
        return state

    def approval_request(self, id: str, *, kind: str, action: str, payload, risk: str, **data) -> dict:
        h = payload_hash(payload)
        prior = self._approval_state(id)
        if prior is not None:
            raise ApprovalStateError(
                f"{id} already exists ({prior['status']}). A changed payload is a new request: "
                f"use a new id and supersedes={id!r}.")
        sup = data.get("supersedes")
        if sup:
            old = self._approval_state(sup)
            if old is None:
                raise ApprovalStateError(f"cannot supersede unknown approval {sup}")
            if old["status"] == "pending":
                self.approval_resolve(sup, "superseded")
                if self.approvals is not None:
                    self.approvals.supersede(sup)
        ev = self.emit("approval", self._clean(id=id, status="pending", kind=kind, action=action, payload=payload,
                                               payload_hash=h, risk=risk, **data))
        if self.approvals is not None:
            self.approvals.submit(who=(self.actor or {}).get("name", "agent"), session=(self.actor or {}).get("session", ""),
                                  what=action, est_cost=float(data.get("cost_usd") or 0), item_id=id, ts=ev["ts"], bound_hash=h,
                                  payload={"egress": data.get("destination", "<unset>"), "model": kind, "payload": payload})
        return ev

    def approval_resolve(self, id: str, status: str, *, decision: str | None = None, decided_by: str | None = None,
                         note: str | None = None) -> dict:
        prior = self._approval_state(id)
        if prior is None:
            raise ApprovalStateError(f"unknown approval {id}")
        if prior["status"] != "pending":
            raise ApprovalStateError(f"{id} is already {prior['status']}; approvals are single-use")
        if status == "pending":
            raise ApprovalStateError("resolve needs a final status")
        data = self._clean(id=id, status=status, kind=prior["kind"], action=prior["action"],
                           payload_hash=prior["payload_hash"], risk=prior["risk"], decision=decision,
                           decided_by=decided_by, decided_at=round(self.clock(), 3), note=note or None)
        return self.emit("approval", data)
