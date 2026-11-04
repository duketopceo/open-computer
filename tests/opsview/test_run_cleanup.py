"""Delete, archive and bulk-clear for goal runs. Fixture runs on disk only: no model, no sandbox."""

import json
import threading

import pytest

from services.opsview import goalruns


def _seed(base, rid, *, state="DONE", failed=False):
    d = base / rid
    (d / "run" / "tasks" / "t1").mkdir(parents=True)
    rows = [{"event": "run_submit", "goal": f"goal {rid}", "subtasks": 1, "ts": 1.0}]
    if failed:
        rows = [{"event": "run_failed", "goal": f"goal {rid}", "error": "boom", "ts": 1.0}]
    else:
        rows.append({"event": "dispatch", "task": "t1", "subtask": "a", "ts": 2.0})
        (d / "run" / "tasks" / "t1" / "status").write_text(state)
    (d / "orchestrator.ndjson").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return d


@pytest.fixture
def goals(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    return tmp_path


def test_delete_removes_run_dir_and_list_entry(goals):
    d = _seed(goals, "goal_00000001")
    assert [r["run"] for r in goalruns.list_runs()] == ["goal_00000001"]
    assert goalruns.delete_run("goal_00000001") is True
    assert not d.exists() and goalruns.list_runs() == []
    assert goalruns.delete_run("goal_00000001") is False


@pytest.mark.parametrize("bad", ["../x", "goal_zz", "", "goal_00000001/../..", "/etc"])
def test_delete_refuses_non_run_ids(goals, bad):
    (goals.parent / "keep").mkdir(exist_ok=True)
    assert goalruns.delete_run(bad) is False
    assert (goals.parent / "keep").exists()


def test_delete_refuses_a_run_being_driven(goals):
    d = _seed(goals, "goal_00000002", state="RUNNING")
    stop = threading.Event()
    t = threading.Thread(target=stop.wait, name="oc-goal-goal_00000002", daemon=True)
    t.start()
    try:
        with pytest.raises(goalruns.RunBusy):
            goalruns.delete_run("goal_00000002")
        assert d.exists()
        assert next(r for r in goalruns.list_runs())["finished"] is False
    finally:
        stop.set()
        t.join()
    assert goalruns.delete_run("goal_00000002") is True  # a stale run with no driver can always go


def test_archive_hides_then_restores_without_touching_the_run(goals):
    d = _seed(goals, "goal_00000003")
    before = (d / "orchestrator.ndjson").read_bytes()
    assert goalruns.archive_run("goal_00000003") is True
    assert goalruns.list_runs() == []
    shown = goalruns.list_runs(include_archived=True)
    assert [(r["run"], r["archived"]) for r in shown] == [("goal_00000003", True)]
    assert goalruns.get_run("goal_00000003") is not None
    assert goalruns.archive_run("goal_00000003", False) is True
    assert [r["run"] for r in goalruns.list_runs()] == ["goal_00000003"]
    assert (d / "orchestrator.ndjson").read_bytes() == before
    assert goalruns.archive_run("goal_deadbeef") is False


def test_clear_finished_keeps_active_runs_and_counts_past_the_list_cap(goals):
    for i in range(35):  # more than the 30-row list cap: bulk clear must not stop at what the rail shows
        _seed(goals, f"goal_{i:08x}")
    _seed(goals, "goal_000000f0", failed=True)
    live = _seed(goals, "goal_000000f1", state="QUEUED")
    (live / "run" / "tasks" / "t1" / "status").write_text("RUNNING")
    stop = threading.Event()
    t = threading.Thread(target=stop.wait, name="oc-goal-goal_000000f1", daemon=True)
    t.start()
    try:
        assert goalruns.clear_finished() == 36
        assert [r["run"] for r in goalruns.list_runs(include_archived=True)] == ["goal_000000f1"]
    finally:
        stop.set()
        t.join()


def test_api_delete_archive_clear(console, monkeypatch):
    base = console.base / "goals"
    base.mkdir(exist_ok=True)
    for rid in ("goal_00000010", "goal_00000011", "goal_00000012"):
        _seed(base, rid)
    csrf = console.csrf()
    hdr = {"Content-Type": "application/json", "X-CSRF-Token": csrf}

    def post(path, body=None):
        s, _, b = console.request("POST", path, body=json.dumps(body or {}), headers=hdr)
        return s, json.loads(b)

    assert post("/api/runs/goal_00000010/archive") == (200, {"ok": True, "archived": True})
    ids = lambda q="": {r["run"] for r in json.loads(console.get("/api/runs" + q)[2])["runs"]}  # noqa: E731
    assert ids() == {"goal_00000011", "goal_00000012"}
    assert ids("?archived=1") == {"goal_00000010", "goal_00000011", "goal_00000012"}
    assert post("/api/runs/goal_00000011/delete") == (200, {"ok": True})
    assert post("/api/runs/goal_00000011/delete")[0] == 404
    assert post("/api/runs/clear")[0] == 400  # needs an explicit confirm
    assert post("/api/runs/clear", {"confirm": True}) == (200, {"cleared": 2})
    assert ids("?archived=1") == set()
