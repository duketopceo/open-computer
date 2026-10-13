"""Work surface checks — the goal run registry's contract (F2).

The UI reads runs off disk. These prove: submit writes a real run,
states render literally, undispatched deps report QUEUED (never a
result), missing runs report nothing, and the goal endpoint is gated.
"""

from __future__ import annotations

import json
import time

from harness import CheckFailed, check


def _eng(_goal: str) -> str:
    return json.dumps(
        [
            {"id": "a", "title": "gather", "prompt": "collect data", "tier": "cheap"},
            {"id": "b", "title": "report", "prompt": "write up", "tier": "mid", "deps": ["a"]},
        ]
    )


def _wait(run_mod, rid, expect: int | None = None, timeout=8.0) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = run_mod.get_run(rid)
        want = expect if expect is not None else (d or {}).get("declared", 0)
        if (d and len(d["subtasks"]) == want and want > 0
                and all(s["state"] in ("DONE", "FAILED") for s in d["subtasks"])):
            return
        time.sleep(0.2)
    raise CheckFailed("run did not settle in time")


@check("WORK", "submit creates a run; list_runs reports it literally")
def submit_appears(runs, tmp_path) -> None:
    rid = runs.submit("test goal", _eng, lambda t: "ok")
    _wait(runs, rid)
    got = [r for r in runs.list_runs() if r["run"] == rid]
    if not got:
        raise CheckFailed("submitted run absent from list_runs")
    r = got[0]
    if r["goal"] != "test goal" or r["subtasks"] != 2:
        raise CheckFailed(f"run record wrong: {r['goal']!r} {r['subtasks']}")
    if r["states"].get("DONE") != 2:
        raise CheckFailed(f"states {r['states']}, wanted DONE:2")


@check("WORK", "get_run reports per-subtask states + results only for DONE")
def run_detail_literal(runs, tmp_path) -> None:
    rid = runs.submit("g", _eng, lambda t: f"did {t['subtask']}")
    _wait(runs, rid)
    d = runs.get_run(rid)
    for s in d["subtasks"]:
        if s["state"] != "DONE" or s["result"] != f"did {s['subtask']}":
            raise CheckFailed(f"subtask {s['subtask']}: {s}")
        if not s["placement"] or not s["reason"]:
            raise CheckFailed(f"subtask {s['subtask']} missing placement/reason")


@check("WORK", "failed dep fails dependents closed — FAILED, never a result")
def blocked_dep_fails(runs, tmp_path) -> None:
    rid = runs.submit(
        "g",
        lambda _g: json.dumps(
            [
                {"id": "a", "title": "fails", "prompt": "p", "tier": "mid"},
                {"id": "b", "title": "blocked", "prompt": "p2", "deps": ["a"]},
            ]
        ),
        lambda t: (_ for _ in ()).throw(RuntimeError("dead")),
    )
    _wait(runs, rid)  # a fails; b dep-fails — the whole run settles
    d = runs.get_run(rid)
    by_id = {s["subtask"]: s for s in d["subtasks"]}
    if by_id["a"]["state"] != "FAILED":
        raise CheckFailed(f"a: {by_id['a']['state']}, wanted FAILED")
    b = by_id.get("b")
    if not b or b["state"] != "FAILED":
        raise CheckFailed(f"b: {b and b['state']}, wanted FAILED (dep-failed)")
    if "result" in b:
        raise CheckFailed("dep-blocked subtask fabricated a result")
    if b.get("error_kind") != "DependencyFailed":
        raise CheckFailed(
            f"b error_kind {b.get('error_kind')!r}, wanted DependencyFailed"
        )
    states = [r["states"] for r in runs.list_runs() if r["run"] == rid]
    if not states or not any(s.get("FAILED") for s in states):
        raise CheckFailed("list_runs hid the failure")


@check("WORK", "missing run reports None — nothing invented")
def missing_run_is_none(runs, tmp_path) -> None:
    if runs.get_run("does-not-exist") is not None:
        raise CheckFailed("get_run fabricated a run that does not exist")


@check("WORK", "goal endpoint is passkey-gated")
def goal_requires_auth(ops) -> None:
    import urllib.error
    import urllib.request

    _b, _a, _get = ops
    r = urllib.request.Request(
        f"{_b}/goals",
        data=b"goal=x&confirm=1",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Host": "remote.test"},
        method="POST",
    )
    try:
        urllib.request.urlopen(r, timeout=5)
        raise CheckFailed("unauthed goal POST accepted")
    except urllib.error.HTTPError as e:
        if e.code != 401:
            raise CheckFailed(f"unauthed goal POST → {e.code}, wanted 401")
