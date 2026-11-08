"""Live view for everyday users: console runs record the redacted event stream without dev trace."""

import json

import pytest

from oc.obs import devtrace
from services.opsview import goalruns, server


@pytest.fixture
def goals(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path / "goals"))
    monkeypatch.delenv("OC_DEV_TRACE", raising=False)
    return tmp_path / "goals"


def _plan(_):
    return json.dumps([{"id": "a", "title": "first", "prompt": "p"}])


def test_live_view_gives_a_run_a_stream_without_dev_trace(goals, monkeypatch):
    monkeypatch.setattr(goalruns, "_start_drive", lambda orch, rid, wfn: None)
    rid = goalruns.submit("goal", _plan, lambda t: "x", live_view=True)
    assert devtrace.enabled_for(run_id=rid) and goalruns.run_events(rid)[0] == 200
    rows = goalruns.run_events(rid)[1]["events"]
    assert rows and any(e["type"] == "step" for e in rows)


def test_default_submit_still_has_no_stream(goals, monkeypatch):
    monkeypatch.setattr(goalruns, "_start_drive", lambda orch, rid, wfn: None)
    rid = goalruns.submit("goal", _plan, lambda t: "x")
    assert goalruns.run_events(rid)[0] == 404


@pytest.mark.parametrize("val,on", [(None, True), ("1", True), ("0", False), ("off", False), ("False", False)])
def test_live_view_setting(monkeypatch, val, on):
    monkeypatch.delenv("OC_LIVE_VIEW", raising=False)
    if val is not None:
        monkeypatch.setenv("OC_LIVE_VIEW", val)
    assert server.live_view_on() is on
