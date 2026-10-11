"""Orchestrator checks — goal->DAG->dispatch->collect contract (U6).

E2E with stub engines only: no network, no model calls. The DAG gates
dispatch (deps tombstone first), worker failure fails closed, and a
pending task is reported QUEUED — never fabricated.
"""

from __future__ import annotations

import json

from harness import CheckFailed, check


def _dag_engine(_goal: str) -> str:
    return json.dumps(
        [
            {"id": "a", "title": "gather", "prompt": "collect data", "tier": "cheap"},
            {"id": "b", "title": "analyze", "prompt": "analyze", "tier": "mid", "deps": ["a"]},
            {
                "id": "c",
                "title": "report",
                "prompt": "write report",
                "tier": "frontier",
                "deps": ["b"],
            },
        ]
    )


def _work(task: dict) -> str:
    return f"did:{task['subtask']}"


@check("ORCH", "E2E: goal decomposes, DAG dispatches in order, results collect")
def e2e_dag(orch, tmp_path) -> None:
    run_id = orch.submit("build it", _dag_engine)
    results = orch.run("build it", _dag_engine, _work)
    for sid in ("a", "b", "c"):
        r = results.get(sid)
        if not r or r["state"] != "DONE":
            raise CheckFailed(f"subtask {sid} state {r and r['state']}, wanted DONE")
        if r["result"] != f"did:{sid}":
            raise CheckFailed(f"subtask {sid} result {r['result']!r}")
    disp = orch.dispatches[run_id]
    if len(disp) != 3 or not all(d.get("placement") and d.get("reason") for d in disp):
        raise CheckFailed("dispatch records missing placement/reason")


@check("ORCH", "dependent subtask stays queued until dep tombstones")
def dep_gates_dispatch(orch, tmp_path) -> None:
    run_id = orch.submit("build it", _dag_engine)
    if len(orch.dispatches[run_id]) != 1:
        raise CheckFailed("deps not enforced — more than 'a' dispatched up front")
    orch.work(_work)  # completes 'a', should then dispatch 'b' only
    states = {d["subtask"] for d in orch.dispatches[run_id]}
    if states != {"a", "b"}:
        raise CheckFailed(f"after one sweep dispatched {states}, wanted a+b")
    orch.work(_work)  # completes 'b', dispatches 'c'
    orch.work(_work)  # completes 'c'
    states = {d["subtask"] for d in orch.dispatches[run_id]}
    if states != {"a", "b", "c"}:
        raise CheckFailed(f"final dispatch set {states}")


@check("ORCH", "malformed decompose output halts — never guessed")
def malformed_decompose(orch, tmp_path) -> None:
    from services.orchestrator import DecomposeError

    for bad in ("not json", '[{"title":"x","deps":["ghost"]}]', "[]", '{"a":1}'):
        try:
            orch.submit("g", lambda _g: bad)
        except DecomposeError:
            continue
        raise CheckFailed(f"bad decompose output accepted: {bad[:40]!r}")


@check("ORCH", "worker failure fails closed — FAILED state, audit row, no fabrication")
def worker_failure_fails_closed(orch, tmp_path) -> None:
    def boom(_task):
        raise RuntimeError("engine died")

    run_id = orch.submit("g", lambda _g: '[{"id":"x","title":"t","prompt":"p"}]')
    orch.work(boom)
    r = orch.results(run_id)["x"]
    if r["state"] != "FAILED":
        raise CheckFailed(f"failed task reported {r['state']}, wanted FAILED")
    if "result" in r:
        raise CheckFailed("failed task carries a fabricated result")
    if not any(
        row.get("event") == "worker_fail" for row in orch.audit.rows()
    ):
        raise CheckFailed("worker failure wrote no audit row")


@check("ORCH", "undispatched/pending subtask reports literal QUEUED, never a result")
def pending_reports_queued(orch, tmp_path) -> None:
    run_id = orch.submit("build it", _dag_engine)
    r = orch.results(run_id)
    if r["b"]["state"] != "QUEUED" or r["c"]["state"] != "QUEUED":
        raise CheckFailed(f"pending subtasks report {r['b']['state']}, {r['c']['state']}")
    if "result" in r["b"] or "result" in r["c"]:
        raise CheckFailed("queued subtask fabricated a result")
