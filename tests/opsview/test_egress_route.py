"""R4 through the console: a goal run with an unapproved host shows ONE Approvals card and waits; approving
that card over HTTP grants the host and the run finishes. Fakes only: stub planner and worker, tmp policy dir."""

from __future__ import annotations

import json
import time

import pytest

from services.execplane.policy import PolicyEngine
from services.gate.queue import ApprovalQueue
from services.opsview import goalruns
from services.opsview.agentgrants import policy_path

JSON = {"Content-Type": "application/json", "Accept": "application/json"}


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_EXECPLANE_DIR", str(tmp_path / "exec"))
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path / "goals"))
    monkeypatch.delenv("OC_EGRESS_ALLOW", raising=False)
    monkeypatch.setenv("OC_EGRESS_APPROVAL_WAIT_S", "5")  # a test run never leaves a driver thread waiting a day


def _decide(console, item, verb, **body):
    body = {"hash": item["payload_hash"], **body}
    return console.request("POST", f"/api/approvals/{item['id']}/{verb}", body=json.dumps(body),
                           headers={**JSON, "X-CSRF-Token": console.csrf()})


def _mine(console, rid):
    q = ApprovalQueue(console.env["OC_APPROVALS"])
    return next(i for i in q.pending() if i["session"] == rid)


def _plan(_prompt):
    return json.dumps([
        {"id": "a", "title": "fetch", "prompt": "p1", "needs_egress": True, "egress_hosts": ["kaggle.com", "github.com"]},
        {"id": "b", "title": "summarise", "prompt": "p2", "deps": ["a"]},
    ])


def _settled(rid):
    for _ in range(300):
        got = goalruns.get_run(rid)
        if got and len(got["subtasks"]) == 2 and all(s["state"] in ("DONE", "FAILED") for s in got["subtasks"]):
            return got
        time.sleep(0.05)
    return goalruns.get_run(rid)


def test_approve_over_http_grants_the_host_and_the_run_completes(console):
    rid = goalruns.submit("g", _plan, lambda t: f"out:{t['subtask']}", by="test")
    item = _mine(console, rid)
    assert item["payload"]["hosts"] == ["kaggle.com"] and item["who"] == "orchestrator"
    _, _, body = console.get(f"/api/runs/{rid}")
    waiting = json.loads(body)["subtasks"]
    assert [(s["subtask"], s["state"]) for s in waiting] == [("a", "AWAITING-APPROVAL")]
    status, _, _ = _decide(console, item, "approve")
    assert status == 200
    assert "kaggle.com" in PolicyEngine(policy_path()).approved_egress()
    got = _settled(rid)
    assert [s["state"] for s in got["subtasks"]] == ["DONE", "DONE"]
    assert got["worker_failures"] == []


def test_send_back_over_http_grants_nothing_and_the_step_fails_with_the_reason(console):
    rid = goalruns.submit("g", _plan, lambda t: "out", by="test")
    item = _mine(console, rid)
    status, _, _ = _decide(console, item, "send-back", note="not that site")
    assert status == 200 and not policy_path().exists()
    got = _settled(rid)
    first = got["subtasks"][0]
    assert first["state"] == "FAILED" and "kaggle.com" in first["error"]


def test_a_refused_grant_leaves_the_card_pending(console, monkeypatch):
    rid = goalruns.submit("g", _plan, lambda t: "out", by="test")
    item = _mine(console, rid)
    monkeypatch.setenv("OC_EXECPLANE_DIR", "/proc/nope/exec")  # unwritable policy dir
    status, _, body = _decide(console, item, "approve")
    assert status == 500 and "could not grant" in json.loads(body)["error"]
    assert _mine(console, rid)["id"] == item["id"]  # still pending: the human can retry
