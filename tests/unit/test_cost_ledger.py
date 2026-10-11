"""OC-035: worker cost ledger. Cap pauses, never cancels; total never exceeds cap by more than one call."""

from __future__ import annotations

import json
import threading

import pytest

from services.cost import CostLedger, LedgerCorrupt, Usage
from services.orchestrator import Orchestrator, WorkResult

def U(c):
    return Usage("deepseek/deepseek-v4-pro", 100, 50, c)


@pytest.fixture
def ledger(tmp_path):
    events = []
    led = CostLedger(tmp_path / "cost.ndjson", default_cap=0.010, emit=events.append)
    led.events = events
    return led


def test_totals_per_run_and_subtask(ledger):
    ledger.record("r1", "a", U(0.002))
    ledger.record("r1", "a", U(0.001))
    ledger.record("r1", "b", U(0.004))
    ledger.record("r2", "a", U(0.5))
    assert ledger.total("r1") == pytest.approx(0.007)
    assert ledger.by_subtask("r1") == pytest.approx({"a": 0.003, "b": 0.004})


def test_cap_is_never_exceeded_by_more_than_one_call(ledger):
    est = 0.003
    started = 0
    while ledger.check("r", est):
        started += 1
        ledger.record("r", "s", U(est))
        assert started < 20
    st = ledger.state("r")
    assert st.state == "paused_cap"
    assert st.spend <= st.cap  # estimates were exact here...
    assert st.spend + est > st.cap  # ...and one more call would not fit


def test_overrun_by_the_in_flight_call_is_recorded_and_pauses(ledger):
    assert ledger.check("r", 0.001)
    st = ledger.record("r", "s", U(0.0125))  # cost exceeded the estimate: truth is recorded
    assert st.state == "paused_cap" and st.spend == pytest.approx(0.0125)
    assert [e["event"] for e in ledger.events].count("run_paused") == 1


def test_pause_is_recorded_once_and_survives_restart(ledger, tmp_path):
    ledger.record("r", "s", U(0.011))
    assert not ledger.check("r", 0.0) and not ledger.check("r", 0.0)
    again = CostLedger(tmp_path / "cost.ndjson", default_cap=0.010)
    assert again.state("r").state == "paused_cap"
    rows = [json.loads(line) for line in (tmp_path / "cost.ndjson").read_text().splitlines()]
    assert [r["event"] for r in rows].count("run_paused") == 1


def test_resume_needs_a_cap_above_spend_then_continues(ledger):
    ledger.record("r", "s", U(0.011))
    with pytest.raises(ValueError):
        ledger.resume("r", 0.011, by="luke")
    st = ledger.resume("r", 0.05, by="luke")
    assert st.state == "running" and st.cap == 0.05
    assert ledger.check("r", 0.01)
    assert [e["event"] for e in ledger.events][-2:] == ["cap_set", "run_resumed"]


def test_corrupt_ledger_fails_closed(ledger, tmp_path):
    ledger.record("r", "s", U(0.001))
    with open(tmp_path / "cost.ndjson", "a") as f:
        f.write("not json\n")
    assert ledger.state("r").state == "paused_cap"
    assert not ledger.check("r", 0.0)
    with pytest.raises(LedgerCorrupt):
        ledger.by_subtask("r")


def test_emitter_failure_never_changes_accounting(tmp_path):
    def boom(_):
        raise RuntimeError("sse client gone")

    led = CostLedger(tmp_path / "c.ndjson", default_cap=1.0, emit=boom)
    led.record("r", "s", U(0.25))
    assert led.total("r") == 0.25


def test_concurrent_checks_respect_the_cap(tmp_path):
    led = CostLedger(tmp_path / "c.ndjson", default_cap=0.010)
    admitted, lock = [], threading.Lock()

    def worker():
        if led.check("r", 0.004):
            led.record("r", "s", U(0.004))
            with lock:
                admitted.append(1)

    ts = [threading.Thread(target=worker) for _ in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(admitted) == 2  # 0.004 + 0.004 fits; a third would be 0.012 > 0.010
    assert led.total("r") <= 0.010


# --- integration with the orchestrator: pause holds work, cancels nothing ----------------------------------

def _engine(_goal):
    return json.dumps([
        {"id": "a", "title": "a", "prompt": "collect data", "tier": "cheap", "est_cost": 0.004},
        {"id": "b", "title": "b", "prompt": "analyze", "tier": "cheap", "deps": ["a"], "est_cost": 0.004},
        {"id": "c", "title": "c", "prompt": "report", "tier": "cheap", "deps": ["b"], "est_cost": 0.004},
    ])


def _work(task):
    return WorkResult(f"did:{task['subtask']}", U(0.006))  # costs more than estimated


def test_run_pauses_at_cap_holds_the_rest_and_resumes_to_completion(tmp_path):
    led = CostLedger(tmp_path / "cost.ndjson", default_cap=0.010)
    orch = Orchestrator(tmp_path / "o", ledger=led)
    res = orch.run("g", _engine, _work)
    states = {k: v["state"] for k, v in res.items()}
    run_id = next(iter(orch.dispatches))
    assert led.state(run_id).state == "paused_cap"
    assert states["a"] == "DONE" and states["b"] == "DONE"
    assert states["c"] != "DONE" and states["c"] != "FAILED"  # held, not cancelled, not failed
    assert led.total(run_id) == pytest.approx(0.012)  # one call over: the one in flight when the cap hit
    led.resume(run_id, 0.05, by="luke")
    orch.work(_work)
    assert orch.results(run_id)["c"]["state"] == "DONE"
    assert orch.results(run_id)["c"]["result"] == "did:c"
