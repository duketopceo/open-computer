"""Router truth: why an engine was skipped (status and a short reason), the decider model override, and a
decision time in milliseconds. Everything runs against the in-process fake provider; no live model."""

import json
import time

import pytest

from fake_openrouter import FakeOpenRouter
from services.router import default_router
from services.router import decide as _decide

NO_ENDPOINTS = (404, {"error": {"code": 404, "message": "No endpoints found for perplexity/pplx-decider-v1.1-27b"}})


def answer(model="typesafe/jev-1.13"):
    return {"answers": {"route": {"choice": "cloud", "confidence": 0.8}, "tier": {"choice": "mid"}},
            "model": model, "usage": {"cost": 1e-6}}


@pytest.fixture
def fake():
    with FakeOpenRouter() as f:
        yield f


def make_router(fake, tmp_path):
    return default_router(tmp_path / "ledger.jsonl", base_url=fake.url.removesuffix("/v1"), api_key="k")


def asked(fake):
    return [r["body"]["model"] for r in fake.requests if r["path"].endswith("/alpha/decisions")]


def test_decider_404_is_recorded_as_the_cause_when_jev_answers(fake, tmp_path):
    fake.chat_replies += [NO_ENDPOINTS, answer()]
    d = make_router(fake, tmp_path).route("summarise this")
    assert d.engine == "jev" and d.model_used == "typesafe/jev-1.13"
    (fb,) = d.fallbacks
    assert (fb["engine"], fb["kind"], fb["status"]) == ("decider", "EngineDown", 404)
    assert fb["reason"] == "No endpoints found for perplexity/pplx-decider-v1.1-27b"


def test_a_404_no_endpoints_decider_is_not_asked_again_but_still_reported(fake, tmp_path):
    fake.chat_replies += [NO_ENDPOINTS, answer(), answer()]
    router = make_router(fake, tmp_path)
    router.route("first")
    d = router.route("second")
    assert asked(fake) == [_decide.DECIDER_MODEL, _decide.JEV_MODEL, _decide.JEV_MODEL]  # decider asked once
    assert d.engine == "jev" and d.fallbacks[0]["status"] == 404  # every step still shows why


def test_a_500_from_the_decider_is_retried_on_the_next_call(fake, tmp_path):
    fake.chat_replies += [500, answer(), answer("perplexity/pplx-decider-v1.1-27b")]
    router = make_router(fake, tmp_path)
    first = router.route("first")
    second = router.route("second")
    assert first.engine == "jev" and first.fallbacks[0]["status"] == 500
    assert second.engine == "decider" and second.fallbacks == []
    assert asked(fake).count(_decide.DECIDER_MODEL) == 2


def test_the_decider_model_can_be_overridden(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("OC_DECIDER_MODEL", "vendor/other-decider")
    fake.chat_replies.append(answer("vendor/other-decider"))
    d = make_router(fake, tmp_path).route("hello")
    assert asked(fake) == ["vendor/other-decider"] and d.engine == "decider" and d.router_model == "vendor/other-decider"


def test_decision_time_is_in_milliseconds(monkeypatch):
    def slow_post(*a, **k):
        time.sleep(0.25)
        return answer(), {}

    monkeypatch.setattr(_decide, "or_post", slow_post)
    out = _decide.ask_decisions("state", api_key="k")
    assert 240 <= out["ms"] < 1000


def test_the_404_cause_is_logged_with_its_status(fake, tmp_path, capsys):
    from oc.obs import log as obs_log

    obs_log.configure(force=True)
    fake.chat_replies += [NO_ENDPOINTS, answer()]
    make_router(fake, tmp_path).route("summarise my private notes")
    rows = [json.loads(ln) for ln in capsys.readouterr().err.splitlines() if ln.startswith("{")]
    (warn,) = [r for r in rows if r["service"] == "router" and r["msg"] == "engine down, trying next"]
    assert warn["engine"] == "decider" and warn["status"] == 404
    assert "private" not in json.dumps([r for r in rows if r["service"] == "router"])
