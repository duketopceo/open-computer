"""services.openrouter: attribution on every call, complete call records, cost truth, reconciliation."""

import json
import urllib.request

import pytest

from oc import obs
from services.openrouter import CallStore, OpenRouterError, chat, collect_usage, post, summarize, total_usage
from services.openrouter import attribution
from services.openrouter.__main__ import main as cli
from services.openrouter.generation import compare, fetch_generation, reconcile
from services.router.decide import EngineDown, ask_decisions

from fake_openrouter import FakeOpenRouter, chat_reply


@pytest.fixture
def fake(monkeypatch):
    with FakeOpenRouter() as f:
        monkeypatch.setenv("OC_OPENROUTER_BASE_URL", f.url)
        yield f


def hdr(req, name):
    return {k.lower(): v for k, v in req["headers"].items()}.get(name.lower())


# --- attribution ---------------------------------------------------------------------------------
def test_attribution_headers_default_and_configurable(fake, monkeypatch):
    chat("m", "hi", key="k")
    r = fake.requests[-1]
    assert hdr(r, "HTTP-Referer") == attribution.DEFAULT_REFERER
    assert hdr(r, "X-OpenRouter-Title") == "Open Computer" == hdr(r, "X-Title")
    assert hdr(r, "X-OpenRouter-Categories") is None and hdr(r, "X-OpenRouter-App-Visibility") is None
    assert hdr(r, "Authorization") == "Bearer k"

    monkeypatch.setenv("OC_OPENROUTER_REFERER", "https://example.org/my-app")
    monkeypatch.setenv("OC_OPENROUTER_TITLE", "My App")
    monkeypatch.setenv("OC_OPENROUTER_CATEGORIES", "cli-agent, not-a-category, personal-agent, cloud-agent")
    monkeypatch.setenv("OC_OPENROUTER_APP_VISIBILITY", "hidden")
    chat("m", "hi", key="k")
    r = fake.requests[-1]
    assert hdr(r, "HTTP-Referer") == "https://example.org/my-app" and hdr(r, "X-OpenRouter-Title") == "My App"
    assert hdr(r, "X-OpenRouter-Categories") == "cli-agent,personal-agent"  # validated, max 2
    assert hdr(r, "X-OpenRouter-App-Visibility") == "hidden"


def test_attribution_can_be_switched_off(fake, monkeypatch):
    monkeypatch.setenv("OC_OPENROUTER_ATTRIBUTION", "off")
    monkeypatch.setenv("OC_OPENROUTER_USER", "someone@example.com")
    with obs.bind(run_id="r", chat_id="c"):
        chat("m", "hi", key="k")
    r = fake.requests[-1]
    assert hdr(r, "HTTP-Referer") is None and hdr(r, "X-Title") is None
    assert not {"session_id", "user", "trace"} & set(r["body"])


def test_request_body_carries_session_user_and_trace(fake, monkeypatch):
    monkeypatch.setenv("OC_OPENROUTER_USER", "someone@example.com")
    monkeypatch.setenv("OC_OPENROUTER_ENVIRONMENT", "dev")
    with obs.bind(run_id="run7", subtask="s3", chat_id="chat-42"):
        chat("vendor/m", "hi", key="k")
    b = fake.requests[-1]["body"]
    assert b["session_id"] == "chat-42"
    assert b["user"].startswith("oc_") and "someone" not in b["user"] and len(b["user"]) <= 128
    t = b["trace"]
    assert t["trace_id"] == obs.trace_id_for("run7") and t["environment"] == "dev"
    assert (t["oc_run"], t["oc_subtask"], t["oc_chat"]) == ("run7", "s3", "chat-42")
    assert b["model"] == "vendor/m" and b["messages"] == [{"role": "user", "content": "hi"}]


def test_explicit_payload_fields_win_over_defaults(fake):
    post(f"{fake.url}/chat/completions", {"model": "m", "messages": [], "session_id": "mine"}, key="k")
    assert fake.requests[-1]["body"]["session_id"] == "mine"


def test_attribution_on_the_router_decisions_call(fake, monkeypatch):
    fake.chat_replies.append({"answers": {"route": {"choice": "cloud", "confidence": 0.9}, "tier": {"choice": "mid"}},
                              "model": "perplexity/x", "usage": {"cost": 2e-6}})
    base = fake.url.removesuffix("/v1")
    out = ask_decisions("state", base_url=base, api_key="k")
    r = fake.requests[-1]
    assert r["path"].endswith("/alpha/decisions")
    assert hdr(r, "HTTP-Referer") == attribution.DEFAULT_REFERER and hdr(r, "X-OpenRouter-Title")
    assert "session_id" not in r["body"] and "trace" not in r["body"]  # headers only for this endpoint
    assert out["cost"] == 2e-6
    (row,) = CallStore().calls()
    assert row["kind"] == "decision" and row["cost"] == 2e-6
    fake.chat_replies.append(500)
    with pytest.raises(EngineDown):
        ask_decisions("state", base_url=base, api_key="k")


# --- call record ---------------------------------------------------------------------------------
def test_call_row_is_complete_and_attributed(fake):
    fake.chat_replies.append(chat_reply("hello", cost=0.0042, model="vendor/served", gid="gen-abc123", provider="Acme",
                                        tokens_in=100, tokens_out=40))
    with obs.bind(run_id="runA", subtask="s1", task="t1", chat_id="chatA", service="worker"):
        res = chat("vendor/requested", "secret prompt text", key="k")
    assert (res.text, res.cost, res.cost_source, res.generation_id) == ("hello", 0.0042, "provider", "gen-abc123")
    assert res.usage.tokens_in == 100 and res.usage.cost == 0.0042
    (row,) = CallStore().calls()
    expect = {"status": "ok", "kind": "chat", "model_requested": "vendor/requested", "model_served": "vendor/served",
              "provider": "Acme", "generation_id": "gen-abc123", "finish_reason": "stop", "tokens_in": 100,
              "tokens_out": 40, "tokens_cached": 2, "tokens_reasoning": 1, "cost": 0.0042, "cost_source": "provider",
              "run_id": "runA", "subtask": "s1", "task": "t1", "chat_id": "chatA", "trace_id": obs.trace_id_for("runA"),
              "reconcile_status": "unreconciled"}
    assert {k: row[k] for k in expect} == expect
    assert row["latency_ms"] >= 0 and row["ts"] > 0 and len(row["prompt_sha256"]) == 16
    assert "prompt" not in row and "completion" not in row  # content is opt-in
    assert "secret prompt text" not in json.dumps(row)


def test_content_capture_is_opt_in_and_redacted(fake, monkeypatch):
    monkeypatch.setenv("OC_OBS_CAPTURE_CONTENT", "1")
    fake.chat_replies.append(chat_reply("reply with sk-or-v1-leakleakleakleak12 inside"))
    chat("m", "my key is sk-or-v1-promptkeypromptkey99", key="k")
    raw = CallStore().path.read_text()
    assert "my key is" in raw and "sk-or-v1-" not in raw


def test_missing_cost_is_recorded_not_guessed(fake):
    fake.chat_replies.append(chat_reply("x", with_cost=False))
    with collect_usage() as used:
        res = chat("m", "p", key="k")
    assert res.cost == 0.0 and res.cost_source == "none"
    (row,) = CallStore().calls()
    assert row["cost_source"] == "none"
    (g,) = summarize(CallStore().calls(), "model")
    assert g["no_cost_reported"] == 1
    assert total_usage(used).cost == 0.0 and total_usage([]) is None


@pytest.mark.parametrize("reply,klass,status", [(429, "http_429", 429), (500, "http_500", 500),
                                                ("garbage", "bad_response", None)])
def test_failures_are_recorded_and_raised(fake, reply, klass, status):
    fake.chat_replies.append(reply)
    with obs.bind(run_id="r", chat_id="c"), pytest.raises(OpenRouterError) as ei:
        chat("m", "p", key="k")
    assert ei.value.error_class == klass and ei.value.status == status
    (row,) = CallStore().calls()
    assert row["status"] == "error" and row["error_class"] == klass and row["cost"] == 0.0 and row["chat_id"] == "c"
    assert "sk-or-v1-leakleak" not in json.dumps(row)


def test_network_error_is_recorded(monkeypatch):
    monkeypatch.setenv("OC_OPENROUTER_BASE_URL", "http://127.0.0.1:9/api/v1")  # nothing listens
    with pytest.raises(OpenRouterError) as ei:
        chat("m", "p", key="k", timeout=2)
    assert ei.value.error_class in {"network", "timeout"}
    assert CallStore().calls()[0]["status"] == "error"


def test_malformed_choices_raise_after_recording(fake):
    fake.chat_replies.append({"id": "gen-x", "model": "m", "usage": {"cost": 0.1, "prompt_tokens": 1}})
    with pytest.raises(OpenRouterError, match="unexpected chat response"):
        chat("m", "p", key="k")
    assert CallStore().calls()[0]["cost"] == 0.1  # money was spent even though the shape was wrong


def test_recording_failure_never_breaks_the_call(fake, monkeypatch):
    monkeypatch.setenv("OC_OBS_DIR", "/proc/definitely/not/writable")
    before = obs.failures()
    assert chat("m", "p", key="k").text == "ok"
    assert obs.failures() > before


def test_store_survives_torn_line_and_groups(fake):
    with obs.bind(run_id="r1", chat_id="chatA"):
        chat("m1", "p", key="k")
        chat("m2", "p", key="k")
    with obs.bind(run_id="r2", chat_id="chatB"):
        fake.chat_replies.append(chat_reply(cost=0.5))
        chat("m1", "p", key="k")
    s = CallStore()
    with open(s.path, "a") as f:
        f.write('{"event":"call","torn')
    calls = s.calls()
    assert len(calls) == 3
    by_chat = {g["key"]: g for g in summarize(calls, "chat")}
    assert by_chat["chatB"]["cost"] == 0.5 and by_chat["chatA"]["calls"] == 2
    assert summarize(calls, "chat")[0]["key"] == "chatB"  # sorted by cost
    assert {g["key"] for g in summarize(calls, "model")} == {"m1", "m2", "vendor/model"}  # grouped by served model
    assert [g["calls"] for g in summarize(calls, "run", run="r1")] == [2]
    assert len(summarize(calls, "day")) == 1
    with pytest.raises(ValueError):
        summarize(calls, "nope")


# --- reconcile -----------------------------------------------------------------------------------
def test_compare_statuses():
    call = {"cost": 0.01, "tokens_in": 10, "tokens_out": 5}
    ok = compare(call, {"total_cost": 0.01, "tokens_prompt": 10, "tokens_completion": 5, "latency": 900})
    assert ok["status"] == "reconciled" and ok["stats_latency_ms"] == 900
    bad = compare(call, {"total_cost": 0.02, "tokens_prompt": 11, "tokens_completion": 5})
    assert bad["status"] == "mismatch" and bad["mismatch_fields"] == ["cost", "tokens_in"]
    assert compare(call, {"total_cost": 0.0100001})["status"] == "reconciled"  # within tolerance


def test_fetch_generation_retries_404_then_succeeds(fake):
    fake.generations["gen-1"] = {"id": "gen-1", "total_cost": 0.1}
    fake.generation_404s = 2
    sleeps = []
    assert fetch_generation("gen-1", "k", sleep=sleeps.append)["total_cost"] == 0.1
    assert sleeps == [2.0, 4.0]
    assert fetch_generation("gen-unknown", "k", attempts=2, sleep=lambda s: None) is None
    assert hdr(fake.requests[-1], "HTTP-Referer")


def test_reconcile_end_to_end(fake):
    fake.chat_replies += [chat_reply(cost=0.01, gid="gen-a", tokens_in=10, tokens_out=5),
                          chat_reply(cost=0.02, gid="gen-b", tokens_in=10, tokens_out=5),
                          chat_reply(cost=0.03, gid="gen-c", tokens_in=10, tokens_out=5)]
    for _ in range(3):
        chat("m", "p", key="k")
    fake.generations["gen-a"] = {"total_cost": 0.01, "tokens_prompt": 10, "tokens_completion": 5, "latency": 800,
                                 "provider_name": "Acme"}
    fake.generations["gen-b"] = {"total_cost": 0.05, "tokens_prompt": 10, "tokens_completion": 5}
    store = CallStore()
    assert reconcile(store, "k", min_age=1e9) == {"reconciled": 0, "mismatch": 0, "missing": 0, "skipped": 0}  # too fresh
    sleepless = lambda gid, key: fetch_generation(gid, key, attempts=1, sleep=lambda s: None)  # noqa: E731
    assert reconcile(store, "k", min_age=0, fetch=sleepless) == {"reconciled": 1, "mismatch": 1, "missing": 1, "skipped": 0}
    status = {c["generation_id"]: c["reconcile_status"] for c in store.calls()}
    assert status == {"gen-a": "reconciled", "gen-b": "mismatch", "gen-c": "missing"}
    fake.generations["gen-c"] = {"total_cost": 0.03, "tokens_prompt": 10, "tokens_completion": 5}
    assert reconcile(store, "k", min_age=0, fetch=sleepless)["reconciled"] == 1  # missing is retried, done is not
    assert {c["reconcile_status"] for c in store.calls()} == {"reconciled", "mismatch"}
    assert summarize(store.calls(), "model")[0]["mismatches"] == 1


def test_cli_report_and_reconcile_without_key(fake, capsys, monkeypatch):
    with obs.bind(run_id="r1", chat_id="chatZ"):
        chat("m", "p", key="k")
    assert cli(["report", "--by", "chat"]) == 0
    out = capsys.readouterr().out
    assert "chatZ" in out and "store:" in out
    assert cli(["report", "--by", "model", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["calls"] == 1
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert cli(["reconcile"]) == 2


def test_no_stray_openrouter_urlopen_outside_the_client():
    """Every OpenRouter call goes through services.openrouter (or the bench helper that imports attribution)."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2]
    for p in list((root / "services").rglob("*.py")):
        if "openrouter" in p.parts:
            continue
        text = p.read_text()
        assert not re.search(r"openrouter\.ai[^\n]*\n[^\n]*urlopen|urlopen[^\n]*openrouter\.ai", text), p
    assert urllib.request  # (keeps the import used)
