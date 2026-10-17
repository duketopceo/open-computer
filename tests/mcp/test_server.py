"""OC-042: MCP server. Golden protocol output, tool-call replay, and the human-only approval rule."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from services.gate import Gate
from services.gate.queue import ApprovalQueue
from services.mcp import server
from services.mcp.server import FORBIDDEN, State, TOOLS, handle

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).parent / "golden_tools_list.json"


@pytest.fixture
def st(tmp_path, monkeypatch):
    for k, f in (
        ("OC_AUDIT", "audit.ndjson"),
        ("OC_APPROVALS", "approvals.ndjson"),
        ("OC_COST_LEDGER", "cost.ndjson"),
        ("OC_ORCH_AUDIT", "orch.ndjson"),
    ):
        monkeypatch.setenv(k, str(tmp_path / f))
    return State()


def call(st, name, **args):
    r = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}, st)
    return r


def payload(r):
    return json.loads(r["result"]["content"][0]["text"])


def test_initialize_golden(st):
    r = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, st)
    assert r["result"]["protocolVersion"] == "2025-06-18"
    assert r["result"]["serverInfo"] == {"name": "open-computer", "version": "0.0.0"}
    assert r["result"]["capabilities"] == {"tools": {}}
    assert "human-only" in r["result"]["instructions"]


def test_tools_list_matches_the_golden_file(st):
    got = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, st)["result"]
    if os.environ.get("OC_UPDATE_GOLDEN"):
        GOLDEN.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n")
    assert got == json.loads(GOLDEN.read_text()), "tools/list changed: review it, then OC_UPDATE_GOLDEN=1"


def test_no_tool_can_approve_decide_apply_or_send():
    for name in TOOLS:
        assert not any(w in name for w in FORBIDDEN), name
    for name in ("approve", "gate_approve", "send_back", "decide", "grant", "apply_policy", "send"):
        r = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": name}}, State())
        assert r["error"]["code"] == -32602


def test_server_source_never_touches_decision_or_send_paths():
    tree = ast.parse((ROOT / "services/mcp/server.py").read_text())
    banned = {"grant", "deny", "decide", "request_cloud_call", "send"}
    used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {
        n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
    }
    assert not (banned & used), banned & used


def test_request_approval_queues_but_cannot_decide_or_send(st):
    r = call(
        st,
        "request_approval",
        what="Summarise notes",
        payload={"model": "x-ai/grok-4.7", "egress": "openrouter.ai", "prompt": "mail jane@example.com"},
        who="agent-1",
        est_cost=0.002,
    )
    out = payload(r)
    assert out["status"] == "pending" and out["pii"] == ["email"] and len(out["payload_hash"]) == 64
    assert payload(call(st, "approval_status", id=out["id"]))["status"] == "pending"
    assert [a["id"] for a in payload(call(st, "list_approvals"))["approvals"]] == [out["id"]]
    audit = Path(os.environ["OC_AUDIT"])
    rows = [json.loads(ln) for ln in audit.read_text().splitlines()] if audit.exists() else []
    assert not any(r.get("event") in ("human_decision", "cloud_call_allowed") for r in rows)


def test_only_the_human_path_changes_status_and_the_client_sees_it(st):
    out = payload(call(st, "request_approval", what="w", payload={"model": "m", "prompt": "hi"}))
    q = ApprovalQueue(st.approvals, Gate(st.audit))
    q.decide(out["id"], approve=True, by="luke", expect_hash=out["payload_hash"])  # the console's act, not the MCP's
    assert payload(call(st, "approval_status", id=out["id"]))["status"] == "approved"
    assert payload(call(st, "list_approvals"))["approvals"] == []  # default view is pending only


def test_queue_without_a_gate_cannot_decide(st):
    out = payload(call(st, "request_approval", what="w", payload={"model": "m"}))
    with pytest.raises(AssertionError):
        st.queue().decide(out["id"], approve=True, by="mcp")


def test_runs_cost_audit_and_place_tools(st, tmp_path):
    Path(st.orch).write_text(
        "".join(
            json.dumps(r) + "\n"
            for r in [
                {"event": "run_submit", "run": "r1", "goal": "g", "subtasks": 2},
                {"event": "dispatch", "run": "r1", "task": "t1", "subtask": "a"},
            ]
        )
    )
    from services.cost import CostLedger, Usage

    led = CostLedger(st.cost)
    led.set_cap("r1", 0.01)
    led.record("r1", "a", Usage("m", 1, 1, 0.002))
    runs = payload(call(st, "list_runs"))["runs"]
    assert runs[0]["run"] == "r1" and runs[0]["dispatched"] == 1 and runs[0]["cost"]["spend"] == 0.002
    assert payload(call(st, "get_run", run="r1"))["cost_by_subtask"] == {"a": 0.002}
    assert "error" in payload(call(st, "get_run", run="nope"))
    assert payload(call(st, "cost", run="r1"))["state"]["cap"] == 0.01
    Gate(st.audit).audit.append({"event": "x"})
    assert payload(call(st, "audit_tail", n=5))["rows"][-1]["event"] == "x"
    p = payload(call(st, "place", subtask={"tier": "frontier"}, pii_flagged=True))
    assert p["target"].startswith("local")  # the MCP can never mark PII approved


def test_bad_calls_are_tool_errors_not_crashes(st):
    assert call(st, "get_run")["result"]["isError"] is True
    assert handle({"jsonrpc": "2.0", "id": 9, "method": "nope"}, st)["error"]["code"] == -32601
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, st) is None


def test_stdio_roundtrip_as_a_real_subprocess(tmp_path):
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "OC_AUDIT": str(tmp_path / "a"),
        "OC_APPROVALS": str(tmp_path / "p"),
        "OC_COST_LEDGER": str(tmp_path / "c"),
        "OC_ORCH_AUDIT": str(tmp_path / "o"),
    }
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    out = subprocess.run(
        [sys.executable, "-m", "services.mcp.server"],
        input="\n".join(map(json.dumps, msgs)) + "\n",
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=20,
    )
    lines = [json.loads(x) for x in out.stdout.splitlines()]
    assert out.returncode == 0 and [x["id"] for x in lines] == [1, 2]
    assert {t["name"] for t in lines[1]["result"]["tools"]} == set(TOOLS)
    assert server.PROTOCOL == lines[0]["result"]["protocolVersion"]
