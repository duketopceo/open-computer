"""Client-side fallback: when the whole OpenRouter request fails, `chat` walks the remaining chain.

FakeOpenRouter only: no live calls, no key."""

from __future__ import annotations

import pytest

from services.openrouter import CallStore, OpenRouterError, chat, collect_usage
from services.openrouter.client import is_failover_error
from services.router import model_catalog as mc

from fake_openrouter import FakeOpenRouter, chat_reply

PARTICLE = "particle-ai/particle-1"
V41 = "deepseek/deepseek-v4.1-flash"
GEMMA31 = "google/gemma-4-31b-it"
CHAIN = [PARTICLE, V41, GEMMA31]


@pytest.fixture
def fake(monkeypatch):
    with FakeOpenRouter() as f:
        monkeypatch.setenv("OC_OPENROUTER_BASE_URL", f.url)
        yield f


def _no_endpoints(status: int) -> tuple[int, dict]:
    return status, {"error": {"code": status, "message": "No endpoints found supporting your data region."}}


@pytest.mark.parametrize(
    "err,expect",
    [
        (OpenRouterError("no endpoints", status=404), True),
        (OpenRouterError("HTTP 400: No endpoints found supporting your data region.", status=400), True),
        (OpenRouterError("HTTP 400: no endpoints available", status=400), True),
        (OpenRouterError("rate limited", status=429), True),
        (OpenRouterError("upstream", status=500), True),
        (OpenRouterError("bad gateway", status=502), True),
        (OpenRouterError("HTTP 400: invalid request schema", status=400), False),
        (OpenRouterError("timeout", status=None, error_class="timeout"), False),
        (OpenRouterError("payment", status=402), False),
    ],
)
def test_is_failover_error(err, expect):
    assert is_failover_error(err) is expect


def test_a_region_404_on_the_whole_request_is_retried_on_the_next_model(fake):
    fake.chat_replies += [_no_endpoints(404), chat_reply("pong", model=V41, cost=0.0001)]
    with collect_usage() as used:
        res = chat(PARTICLE, "p", key="k", effort="low")
    assert res.model_served == V41 and res.text == "pong"
    assert used[0].model == V41
    # first request is the full native chain; second is the tail
    assert [q["body"].get("models") or [q["body"].get("model")] for q in fake.requests] == [CHAIN, [V41, GEMMA31]]
    rows = CallStore().calls()
    assert [r["status"] for r in rows] == ["error", "ok"]
    assert rows[0]["http_status"] == 404 and rows[0]["model_requested"] == PARTICLE
    assert rows[1]["model_requested"] == PARTICLE and rows[1]["model_served"] == V41
    assert rows[1]["models_requested"] == CHAIN and rows[1]["fell_back"] is True


def test_a_400_no_endpoints_error_is_retried(fake):
    fake.chat_replies += [_no_endpoints(400), chat_reply("ok", model=GEMMA31)]
    res = chat(PARTICLE, "p", key="k", effort="low")
    assert res.model_served == GEMMA31
    assert CallStore().calls()[-1]["fell_back"] is True


def test_a_429_or_503_is_retried(fake):
    fake.chat_replies += [429, chat_reply("ok", model=V41)]
    assert chat(PARTICLE, "p", key="k", effort="low").model_served == V41
    fake.chat_replies += [503, chat_reply("ok", model=V41)]
    assert chat(PARTICLE, "p", key="k", effort="low").model_served == V41
    assert len(fake.requests) == 4


def test_a_400_that_is_not_a_region_error_is_not_retried(fake):
    fake.chat_replies.append((400, {"error": {"code": 400, "message": "invalid request schema"}}))
    with pytest.raises(OpenRouterError) as ei:
        chat(PARTICLE, "p", key="k", effort="low")
    assert ei.value.status == 400
    assert len(fake.requests) == 1
    assert CallStore().calls()[0]["status"] == "error"


def test_every_model_in_the_chain_failing_raises_the_last_error(fake):
    fake.chat_replies += [404, 404, 404]
    with pytest.raises(OpenRouterError) as ei:
        chat(PARTICLE, "p", key="k", effort="low")
    assert ei.value.status == 404
    asked = [q["body"].get("models") or [q["body"].get("model")] for q in fake.requests]
    assert asked == [CHAIN, [V41, GEMMA31], [GEMMA31]]
    assert [r["status"] for r in CallStore().calls()] == ["error", "error", "error"]


def test_native_fallback_on_the_first_request_is_not_retried(fake):
    """OpenRouter already walked the chain and served a later model: one request, no client retry."""
    fake.chat_replies.append(chat_reply("ok", model=GEMMA31))
    res = chat(PARTICLE, "p", key="k", effort="low")
    assert res.model_served == GEMMA31
    assert len(fake.requests) == 1
    (row,) = CallStore().calls()
    assert row["fell_back"] is True and row["models_requested"] == CHAIN


def test_a_model_outside_the_catalog_is_not_retried(fake):
    fake.chat_replies.append(404)
    with pytest.raises(OpenRouterError):
        chat("vendor/alone", "p", key="k")
    assert len(fake.requests) == 1
    assert fake.requests[0]["body"]["model"] == "vendor/alone"


def test_medium_chain_matches_the_catalog():
    assert mc.CATALOG.chain(PARTICLE) == CHAIN
