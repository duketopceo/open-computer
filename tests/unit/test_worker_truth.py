"""Step truth in the audit rows: the dispatch row carries the router decision, and the worker rows carry which
model served the step, who ran it, its tokens and cost, on success, failure and requeue alike."""

import json

import pytest

from fake_openrouter import FakeOpenRouter, chat_reply
from services.cost import Usage
from services.orchestrator import Orchestrator, engines
from services.placement import Placement

PLAN = json.dumps([{"id": "a", "title": "A", "prompt": "do a", "deps": []}])


@pytest.fixture
def fake(monkeypatch):
    with FakeOpenRouter() as f:
        monkeypatch.setenv("OC_OPENROUTER_BASE_URL", f.url)
        yield f


def cloud(*a, **k):
    return Placement("cloud-OpenRouter", "test")


def rows(orch, event):
    return [r for r in orch.audit.rows() if r["event"] == event]


def test_dispatch_and_worker_done_rows_carry_the_decision_and_the_served_model(fake, tmp_path):
    fake.chat_replies += [chat_reply(PLAN, cost=0.0),
                          chat_reply("done", cost=0.0102635, model="deepseek/deepseek-v4-pro", provider="DeepInfra",
                                     tokens_in=30, tokens_out=12)]
    orch = Orchestrator(tmp_path / "o", place_fn=cloud)
    orch.submit("g", engines.decompose_engine(api_key="k"))
    orch.work(engines.work_fn(api_key="k"))
    (dispatch,) = rows(orch, "dispatch")
    assert set(dispatch["router"]) == {"engine", "model", "confidence", "ms", "fallbacks"}
    assert dispatch["router"]["engine"] == dispatch["engine"] and dispatch["router"]["fallbacks"] == []
    (done,) = rows(orch, "worker_done")
    assert done["worker"] == {"model": "deepseek/deepseek-v4-pro", "provider": "DeepInfra", "tokens_in": 30,
                              "tokens_out": 12, "tokens_reasoning": 1, "cost": pytest.approx(0.0102635)}


def _failing(usage):
    def work(task):
        err = RuntimeError("sandbox exec rc=1")
        err.usage = usage  # the contract with work_fn: spend before the failure rides on the exception
        raise err

    return work


def test_a_failed_step_keeps_the_worker_it_paid_for(fake, tmp_path):
    fake.chat_replies.append(chat_reply(PLAN, cost=0.0))
    orch = Orchestrator(tmp_path / "o", place_fn=cloud)
    orch.submit("g", engines.decompose_engine(api_key="k"))
    orch.work(_failing(Usage("vendor/served", 20, 8, 0.004, "Acme", 3)))
    (fail,) = rows(orch, "worker_fail")
    assert fail["worker"] == {"model": "vendor/served", "provider": "Acme", "tokens_in": 20, "tokens_out": 8,
                              "tokens_reasoning": 3, "cost": 0.004}


def test_a_step_that_failed_before_any_call_has_no_worker_object(fake, tmp_path):
    fake.chat_replies.append(chat_reply(PLAN, cost=0.0))
    orch = Orchestrator(tmp_path / "o", place_fn=cloud)
    orch.submit("g", engines.decompose_engine(api_key="k"))
    orch.work(_failing(None))
    (fail,) = rows(orch, "worker_fail")
    assert "worker" not in fail
