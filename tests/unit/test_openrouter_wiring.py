"""The orchestrator's cloud path end to end against a fake OpenRouter: real spend reaches the cost ledger, the cap
reacts to it, planner spend is attributed to the run, and ids flow from submit to the OpenRouter request."""

import json

import pytest

from fake_openrouter import FakeOpenRouter, chat_reply
from services.cost import CostLedger
from services.openrouter import CallStore
from services.orchestrator import Orchestrator, engines
from services.placement import Placement

PLAN = json.dumps([
    {"id": "a", "title": "A", "prompt": "do a", "deps": []},
    {"id": "b", "title": "B", "prompt": "do b", "deps": ["a"]},
])


@pytest.fixture
def fake(monkeypatch):
    with FakeOpenRouter() as f:
        monkeypatch.setenv("OC_OPENROUTER_BASE_URL", f.url)
        yield f


def cloud(*a, **k):
    return Placement("cloud-OpenRouter", "test")


def test_work_fn_returns_provider_usage_for_the_ledger(fake):
    fake.chat_replies.append(chat_reply("done", cost=0.0031, model="vendor/served", tokens_in=7, tokens_out=3))
    out = engines.work_fn(api_key="k")({"placement": "cloud-OpenRouter", "prompt": "p", "run": "r", "subtask": "s"})
    assert out.output == "done"
    assert (out.usage.cost, out.usage.tokens_in, out.usage.tokens_out, out.usage.model) == (0.0031, 7, 3, "vendor/served")


def test_local_placement_reports_no_usage_and_plain_text(monkeypatch):
    monkeypatch.setattr(engines, "_chat", lambda *a, **k: ("local text", 0.0))
    assert engines.work_fn(api_key="k")({"placement": "local-llamacpp", "prompt": "p"}) == "local text"


def test_sandbox_script_writer_spend_is_collected(fake, monkeypatch):
    fake.chat_replies.append(chat_reply("```bash\necho hi\n```", cost=0.002))

    class Plane:
        def sandboxes_available(self):
            return True

        def make_sandbox(self, **kw):
            from services.execplane.client import ExecResult

            class SB:
                def exec(self, cmd):
                    return ExecResult(returncode=0, stdout="hi\n", stderr="")

                def close(self):
                    pass

            return SB()

    monkeypatch.setattr(engines, "_plane", lambda: Plane())
    out = engines.work_fn(api_key="k")({"placement": "cloud-sandbox", "prompt": "say hi"})
    assert out.output == "hi\n" and out.usage.cost == 0.002


def test_full_run_ledger_sees_real_spend_and_ids_reach_openrouter(fake, tmp_path):
    fake.chat_replies += [chat_reply(PLAN, cost=0.001, model="plan/m"),
                          chat_reply("A-done", cost=0.004), chat_reply("B-done", cost=0.006)]
    led = CostLedger(tmp_path / "cost.ndjson", default_cap=1.0)
    orch = Orchestrator(tmp_path / "o", ledger=led, place_fn=cloud)
    run_id = orch.submit("write two things", engines.decompose_engine(api_key="k"), chat_id="chat-7")
    assert orch.work(engines.work_fn(api_key="k")) and orch.work(engines.work_fn(api_key="k"))
    assert led.total(run_id) == pytest.approx(0.011)  # planner 0.001 + workers 0.004 + 0.006: provider-reported
    assert set(led.by_subtask(run_id)) == {"_plan", "a", "b"}
    reqs = [r for r in fake.requests if r["body"]]
    assert [r["body"]["session_id"] for r in reqs] == ["chat-7"] * 3  # planner and both workers share the chat
    assert {r["body"]["trace"]["oc_run"] for r in reqs} == {run_id}
    assert [r["body"]["trace"].get("oc_subtask") for r in reqs] == [None, "a", "b"]
    calls = CallStore().calls()
    assert [c["chat_id"] for c in calls] == ["chat-7"] * 3 and sum(c["cost"] for c in calls) == pytest.approx(0.011)
    assert any(r.get("chat") == "chat-7" for r in orch.audit.rows() if r["event"] == "run_submit")


def test_chat_defaults_to_the_run_and_survives_recover(fake, tmp_path):
    fake.chat_replies.append(chat_reply(PLAN))
    orch = Orchestrator(tmp_path / "o", place_fn=cloud)
    run_id = orch.submit("goal", engines.decompose_engine(api_key="k"))
    assert fake.requests[0]["body"]["session_id"] == run_id
    fake.chat_replies.append(chat_reply(PLAN))
    run2 = Orchestrator(tmp_path / "o2", place_fn=cloud).submit("g2", engines.decompose_engine(api_key="k"),
                                                               chat_id="keepme")
    revived = Orchestrator(tmp_path / "o2", place_fn=cloud)
    assert revived.recover(run2) and revived._chat[run2] == "keepme"


def test_cap_pauses_on_actual_not_estimated_spend(fake, tmp_path):
    # estimates say $0.0001; the provider says $0.009 per call: only real cost can trip a $0.01 cap
    plan = json.dumps([{"id": s, "title": s, "prompt": s, "deps": [], "est_cost": 0.0001} for s in "abc"])
    fake.chat_replies += [chat_reply(plan, cost=0.0)] + [chat_reply("x", cost=0.009) for _ in range(3)]
    led = CostLedger(tmp_path / "cost.ndjson", default_cap=0.010)
    orch = Orchestrator(tmp_path / "o", ledger=led, place_fn=cloud)
    run_id = orch.submit("g", engines.decompose_engine(api_key="k"))
    orch.work(engines.work_fn(api_key="k"))
    st = led.state(run_id)
    assert st.state == "paused_cap" and st.spend == pytest.approx(0.018)  # 2nd call finished over the cap, 3rd held
    assert st.calls == 3  # planner row + two worker rows


def test_obs_failure_does_not_fail_the_run(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("OC_OBS_DIR", "/proc/not/writable")
    fake.chat_replies += [chat_reply(PLAN, cost=0.0), chat_reply("ok", cost=0.001), chat_reply("ok", cost=0.001)]
    led = CostLedger(tmp_path / "cost.ndjson", default_cap=1.0)
    orch = Orchestrator(tmp_path / "o", ledger=led, place_fn=cloud)
    run_id = orch.submit("g", engines.decompose_engine(api_key="k"))
    orch.work(engines.work_fn(api_key="k"))
    orch.work(engines.work_fn(api_key="k"))
    assert led.total(run_id) == pytest.approx(0.002) and all(
        v["state"] == "DONE" for v in orch.results(run_id).values())


def test_missing_key_still_fails_clean(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(engines.WorkerUnavailable):
        engines.work_fn(api_key="")({"placement": "cloud-OpenRouter", "prompt": "p"})
