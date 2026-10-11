"""Silver Lining section 14: no GET request ever causes a side effect.

Two layers: (1) every mutation path is replaced with a tripwire and every GET
route is hit, including hostile query strings; (2) the data directory is
byte-for-byte identical afterwards (a GET may not even create a file)."""

import ast
import inspect
import textwrap

import pytest

from services.execplane import client as exec_client
from services.gate import audit as audit_mod
from services.gate import gate as gate_mod
from services.gate import queue as queue_mod
from services.opsview import runs as runs_mod
from services.opsview import server
from services.router import budget as budget_mod

from test_routing import concrete


class Tripped(AssertionError):
    pass


def trip(name):
    def _t(*a, **k):
        raise Tripped(f"a GET reached {name}")
    return _t


@pytest.fixture
def tripwires(monkeypatch):
    monkeypatch.setattr(server, "_run_matrix", trip("run_matrix (boots microVMs)"))
    monkeypatch.setattr(server, "_route", trip("router (may spend)"))
    monkeypatch.setattr(queue_mod.ApprovalQueue, "decide", trip("ApprovalQueue.decide"))
    monkeypatch.setattr(queue_mod.ApprovalQueue, "submit", trip("ApprovalQueue.submit"))
    monkeypatch.setattr(queue_mod.ApprovalQueue, "_append", trip("ApprovalQueue._append"))
    monkeypatch.setattr(gate_mod.Gate, "__init__", trip("Gate()"))
    monkeypatch.setattr(gate_mod.Gate, "request_cloud_call", trip("Gate.request_cloud_call"))
    monkeypatch.setattr(audit_mod.AuditLog, "__init__", trip("AuditLog() (touches the file)"))
    monkeypatch.setattr(audit_mod.AuditLog, "append", trip("AuditLog.append"))
    monkeypatch.setattr(budget_mod.Budget, "__init__", trip("Budget() (mkdirs)"))
    monkeypatch.setattr(budget_mod.Budget, "record", trip("Budget.record"))
    monkeypatch.setattr(runs_mod, "record", trip("runs.record"))
    monkeypatch.setattr(exec_client.ExecplaneAdapter, "__init__", trip("ExecplaneAdapter()"))


QUERIES = ["", "?q=boom", "?f=blocked", "?run=1", "?confirm=1&q=x", "?approve=1&id=ap_00000000",
           "?inline=1", "?cmd=rm%20-rf"]


def get_paths():
    base = [concrete(p) for p, _ in server.GET_ROUTES]
    return [b + q for b in base for q in QUERIES] + ["/run", "/run?x=1", "/route?q=hello", "/runs/run_zzzzzzzz"]


def test_every_get_is_side_effect_free(console, tripwires):
    before = console.snapshot()
    seen = 0
    for path in get_paths():
        status, _, body = console.get(path)
        assert status in (200, 302, 404, 405), (path, status)
        assert "a GET reached" not in body, path
        seen += 1
    assert seen > 50
    assert console.snapshot() == before, "a GET changed files in the data directory"


def test_old_side_effecting_get_urls_refuse(console, tripwires, calls):
    for path in ("/run", "/route?q=hi"):
        status, headers, _ = console.get(path)
        assert status == 405 and headers["Allow"] == "POST"
    assert calls == {"matrix": 0, "route": 0}


FORBIDDEN = {"_run_matrix", "_route", "decide", "submit", "record", "Gate", "AuditLog", "Budget",
             "subprocess", "write_text", "write", "open", "os.remove", "unlink", "mkdir", "touch", "append"}


def test_get_handlers_do_not_name_a_mutation_path():
    """Static backstop: a new g_* handler that mentions a mutation path fails here
    even if the runtime test above forgot to reach it."""
    for _, name in server.GET_ROUTES:
        src = textwrap.dedent(inspect.getsource(getattr(server.Handler, name)))
        used = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
        assert not (used & FORBIDDEN), (name, used & FORBIDDEN)


def test_readers_never_create_files(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_APPROVALS", str(tmp_path / "none" / "a.ndjson"))
    monkeypatch.setenv("OC_RUNS_DIR", str(tmp_path / "none" / "runs"))
    monkeypatch.setenv("OC_ROUTER_LEDGER", str(tmp_path / "none" / "l.jsonl"))
    assert queue_mod.ApprovalQueue(server.approvals_path()).items() == []
    assert runs_mod.list_runs() == [] and runs_mod.get_run("run_00000000") is None
    assert server.day_spend() == 0.0
    assert not (tmp_path / "none").exists()
