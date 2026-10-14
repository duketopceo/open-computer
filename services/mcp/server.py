"""open-computer MCP server, stdio transport (OC-042).

Exposes the safety primitives to an agent client: goals/runs (read), approvals (request and read, NEVER decide),
cost ledger, audit tail, placement. Hand-rolled JSON-RPC 2.0 (newline-delimited), stdlib only.

THE RULE THIS FILE EXISTS TO KEEP: approval is human-only. There is no tool that approves, sends back, grants,
applies policy or sends a payload, and none may be added: `tests/mcp/test_server.py` fails the build if a tool
name or a code path suggests otherwise. An agent can *request* an approval and *read* its status; the owner
decides in the console. This server never imports `Gate.grant`/`Gate.deny` and never opens an egress.

Config (env): OC_AUDIT, OC_APPROVALS, OC_COST_LEDGER, OC_ORCH_AUDIT, OC_RUN_CAP (default per-run cap, dollars). Run: `uv run python -m services.mcp.server`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from services.cost import CostLedger
from services.gate import Gate
from services.gate.audit import AuditLog
from services.gate.queue import ApprovalQueue
from services.placement import place

PROTOCOL = "2025-06-18"
SERVER_INFO = {"name": "open-computer", "version": "0.0.0"}

# Words that may never appear in a tool name: approval is the owner's act, in the console.
FORBIDDEN = ("approve", "decide", "grant", "send_back", "sendback", "apply", "send", "egress")


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default))


class State:
    """Where the server reads and writes. Paths come from the environment."""

    def __init__(self):
        self.audit = _env_path("OC_AUDIT", "/tmp/oc-audit/audit.ndjson")
        self.approvals = _env_path("OC_APPROVALS", "/tmp/oc-audit/approvals.ndjson")
        self.cost = _env_path("OC_COST_LEDGER", "/tmp/oc-audit/cost.ndjson")
        self.orch = _env_path("OC_ORCH_AUDIT", "/tmp/oc-audit/orchestrator.ndjson")
        self.run_cap = float(os.environ.get("OC_RUN_CAP", "0.10"))

    def queue(self) -> ApprovalQueue:
        # No gate: this queue can submit and read, and `decide` asserts a gate, so it cannot decide.
        return ApprovalQueue(self.approvals, None)


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _public_item(it: dict) -> dict:
    keys = ("id", "status", "who", "what", "model", "egress", "est_cost", "pii", "payload_hash", "ts", "note")
    return {k: it.get(k) for k in keys}


# --- tools ------------------------------------------------------------------------------------------------------

def t_list_runs(st: State, args: dict) -> dict:
    runs: dict[str, dict] = {}
    for r in _rows(st.orch):
        rid = r.get("run")
        if r.get("event") == "run_submit":
            runs[rid] = {"run": rid, "goal": r.get("goal"), "subtasks": r.get("subtasks"), "dispatched": 0}
        elif r.get("event") == "dispatch" and rid in runs:
            runs[rid]["dispatched"] += 1
    for rid, run in runs.items():
        run["cost"] = CostLedger(st.cost, st.run_cap).state(rid).__dict__
    return {"runs": list(runs.values())}


def t_get_run(st: State, args: dict) -> dict:
    rid = args["run"]
    rows = [r for r in _rows(st.orch) if r.get("run") == rid]
    if not rows:
        return {"error": f"no such run: {rid}"}
    led = CostLedger(st.cost, st.run_cap)
    return {
        "run": rid,
        "events": rows,
        "cost": led.state(rid).__dict__,
        "cost_by_subtask": led.by_subtask(rid),
    }


def t_list_approvals(st: State, args: dict) -> dict:
    status = args.get("status", "pending")
    items = st.queue().items()
    if status != "all":
        items = [i for i in items if i["status"] == status]
    return {"approvals": [_public_item(i) for i in items]}


def t_request_approval(st: State, args: dict) -> dict:
    """Queue an escalation for the OWNER to decide. Returns the id and the payload hash the approval will bind to."""
    payload = args["payload"]
    if not isinstance(payload, dict):
        return {"error": "payload must be an object"}
    q = ApprovalQueue(st.approvals, Gate(st.audit))  # Gate used only to PII-scan at submit
    item_id = q.submit(who=str(args.get("who", "mcp-client")), what=str(args["what"]), payload=payload,
                       session=str(args.get("session", "")), est_cost=float(args.get("est_cost", 0.0)))
    item = q.get(item_id)
    return {"id": item_id, "status": "pending", "payload_hash": item["payload_hash"], "pii": item["pii"],
            "next": "the owner decides in the console; poll approval_status. Nothing is sent by this call."}


def t_approval_status(st: State, args: dict) -> dict:
    it = st.queue().get(args["id"])
    return _public_item(it) if it else {"error": f"no such approval: {args['id']}"}


def t_cost(st: State, args: dict) -> dict:
    led = CostLedger(st.cost, st.run_cap)
    return {"state": led.state(args["run"]).__dict__, "by_subtask": led.by_subtask(args["run"])}


def t_audit_tail(st: State, args: dict) -> dict:
    n = max(1, min(int(args.get("n", 20)), 200))
    return {"rows": AuditLog(st.audit).rows()[-n:]}


def t_place(st: State, args: dict) -> dict:
    p = place(args.get("subtask", {}), pii_flagged=bool(args.get("pii_flagged", False)),
              approved=False, headroom=args.get("headroom"))
    return {"target": p.target, "reason": p.reason, "gate_required": p.gate_required}


_OBJ = "object"
TOOLS: dict[str, tuple[str, dict, Callable[[State, dict], dict], bool]] = {
    # name: (description, input schema, handler, read_only)
    "list_runs": ("List orchestrated goals (runs) with dispatch counts and cost state.",
                  {"type": _OBJ, "properties": {}}, t_list_runs, True),
    "get_run": ("Audit events, cost state and per-subtask cost for one run.",
                {"type": _OBJ, "properties": {"run": {"type": "string"}}, "required": ["run"]}, t_get_run, True),
    "list_approvals": ("List approval requests. status: pending (default), approved, sent_back, all.",
                       {"type": _OBJ, "properties": {"status": {"type": "string"}}}, t_list_approvals, True),
    "request_approval": (
        "Ask the owner to approve one exact payload leaving the box. Queues it; sends nothing; cannot be approved by this client.",
        {"type": _OBJ, "required": ["what", "payload"], "properties": {
            "what": {"type": "string"}, "payload": {"type": _OBJ}, "who": {"type": "string"},
            "session": {"type": "string"}, "est_cost": {"type": "number"}}}, t_request_approval, False),
    "approval_status": ("Status of one approval request (pending, approved, sent_back).",
                        {"type": _OBJ, "properties": {"id": {"type": "string"}}, "required": ["id"]}, t_approval_status, True),
    "cost": ("Cost-ledger state (cap, spend, paused) and per-subtask spend for a run.",
             {"type": _OBJ, "properties": {"run": {"type": "string"}}, "required": ["run"]}, t_cost, True),
    "audit_tail": ("Last n rows of the append-only audit log (default 20, max 200).",
                   {"type": _OBJ, "properties": {"n": {"type": "integer"}}}, t_audit_tail, True),
    "place": ("Where a subtask would run (local/cloud, gate required). Pure; never approves PII.",
              {"type": _OBJ, "properties": {"subtask": {"type": _OBJ}, "pii_flagged": {"type": "boolean"},
                                             "headroom": {"type": "number"}}}, t_place, True),
}


def tool_list() -> list[dict]:
    return [
        {"name": n, "description": d, "inputSchema": s, "annotations": {"readOnlyHint": ro, "destructiveHint": False}}
        for n, (d, s, _h, ro) in sorted(TOOLS.items())
    ]


# --- JSON-RPC ---------------------------------------------------------------------------------------------------

def handle(msg: dict, st: State) -> dict | None:
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:  # notification
        return None

    def ok(result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code: int, text: str) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}}

    if method == "initialize":
        return ok({"protocolVersion": PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO,
                   "instructions": "Approval is human-only: request_approval queues a request for the owner. "
                                   "This server has no tool that approves, sends back or sends anything."})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": tool_list()})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        if name not in TOOLS:
            return err(-32602, f"unknown tool: {name}")
        try:
            result = TOOLS[name][2](st, params.get("arguments") or {})
        except KeyError as e:
            return ok({"isError": True, "content": [{"type": "text", "text": f"missing argument: {e.args[0]}"}]})
        except Exception as e:  # tool failure is a tool result, not a protocol error
            return ok({"isError": True, "content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}]})
        return ok({"isError": "error" in result,
                   "content": [{"type": "text", "text": json.dumps(result, sort_keys=True, default=str)}]})
    return err(-32601, f"method not found: {method}")


def serve(stdin=sys.stdin, stdout=sys.stdout, st: State | None = None) -> None:
    st = st or State()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}) + "\n")
            stdout.flush()
            continue
        resp = handle(msg, st)
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()
