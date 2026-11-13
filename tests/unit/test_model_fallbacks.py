"""Fallback chains (catalog `fallbacks` -> OpenRouter `models`), reasoning on Medium/High, the decider chain.

Recorded fixtures and a local fake only: no live calls, no key."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.openrouter import CallStore, chat, collect_usage, effort_scope
from services.router import decide
from services.router import model_catalog as mc
from services.router.router import default_router

from fake_openrouter import FakeOpenRouter, chat_reply
from test_model_catalog import RAW, entry, variant

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "openrouter" / "chat_fallback_served.json"
PARTICLE = "particle-ai/particle-1"
V41 = "deepseek/deepseek-v4.1-flash"
MIMO = "google/gemma-4-31b-it"


@pytest.fixture
def fake(monkeypatch):
    with FakeOpenRouter() as f:
        monkeypatch.setenv("OC_OPENROUTER_BASE_URL", f.url)
        yield f


# --- catalog -------------------------------------------------------------------------------------
def test_particle_1_is_the_default_orchestrator_and_the_medium_preset():
    cat = mc.load()
    assert cat.default_orchestrator == PARTICLE
    assert cat.presets == {"low": "google/gemma-4-26b-a4b-it", "medium": PARTICLE, "high": "z-ai/glm-5.3"}
    p = cat.get(PARTICLE)
    assert (p.input_price, p.output_price, p.context, p.tools, p.reasoning) == (0.05, 0.50, 1048576, True, True)
    assert cat.get("deepseek/deepseek-v4-pro") is not None  # still offered as an option


def test_every_shipped_chain_is_valid_and_every_cloud_model_has_one():
    cat = mc.load()
    assert cat.chain(PARTICLE) == [PARTICLE, V41, MIMO]
    assert cat.chain("google/gemma-4-26b-a4b-it") == ["google/gemma-4-26b-a4b-it", "nvidia/nemotron-3.5-lightning",
                                                      "deepseek/deepseek-v4-flash"]
    assert cat.chain("z-ai/glm-5.3") == ["z-ai/glm-5.3", "xiaomi/mimo-v2.6-pro", "deepseek/deepseek-v4-pro"]
    assert cat.chain("z-ai/glm-5.3-flash") == ["z-ai/glm-5.3-flash", "minimax/minimax-m3.1-flash",
                                              "inclusionai/ling-3.0-flash"]
    for e in cat.entries:
        assert e.fallbacks, e.id
        for f in e.fallbacks:
            fb = cat.get(f)
            assert fb and not fb.above_ceiling and fb.tools and set(e.roles) <= set(fb.roles)


@pytest.mark.parametrize(
    "edit,msg",
    [
        (lambda d: entry(d, PARTICLE).update(fallbacks=["nope/missing"]), "not a catalog entry"),
        (lambda d: entry(d, PARTICLE).update(fallbacks=["x-ai/grok-4.7"]), "above the ceiling"),
        (lambda d: entry(d, PARTICLE).update(fallbacks=["z-ai/glm-5.3-flash"]), "lacks a role"),
        (lambda d: entry(d, PARTICLE).update(fallbacks=[PARTICLE]), "distinct"),
        (lambda d: entry(d, PARTICLE).update(fallbacks=["a/1", "b/2", "c/3"]), "at most 2"),
        (lambda d: entry(d, PARTICLE).update(fallbacks=V41), "list of model ids"),
        (lambda d: entry(d, PARTICLE).update(reasoning="yes"), "reasoning must be"),
    ],
)
def test_bad_chains_refuse_to_load(edit, msg):
    with pytest.raises(mc.CatalogError, match=msg):
        mc.parse(variant(e=edit))


def test_a_fallback_without_tools_is_refused_for_a_tools_model():
    def edit(d):
        entry(d, "inclusionai/ling-3.0-flash")["tools"] = False
        for m in d["models"]:  # only the worker leans on it
            if m["id"] != "z-ai/glm-5.3-flash":
                m["fallbacks"] = [f for f in m.get("fallbacks", []) if f != "inclusionai/ling-3.0-flash"]

    with pytest.raises(mc.CatalogError, match="no tool calling"):
        mc.parse(variant(e=edit))


def test_call_params_chain_and_reasoning_by_effort():
    assert mc.call_params(PARTICLE, "low") == {"models": mc.CATALOG.chain(PARTICLE)}
    assert mc.call_params(PARTICLE, "medium")["reasoning"] == {"effort": "medium"}
    assert mc.call_params(PARTICLE, "high")["reasoning"] == {"effort": "high"}
    assert mc.call_params(PARTICLE)["reasoning"] == {"effort": "medium"}  # unknown effort: its lowest preset level
    assert mc.call_params("z-ai/glm-5.3")["reasoning"] == {"effort": "high"}  # High's own preset
    assert "reasoning" not in mc.call_params("google/gemma-4-26b-a4b-it")  # Low's preset: Low asks none
    assert "reasoning" not in mc.call_params("x-ai/grok-4.7", "high")  # not flagged: left as it was
    assert mc.call_params("typesafe/jev-1.13", "high") == {}  # outside the catalog: untouched


# --- the wire ------------------------------------------------------------------------------------
def test_chat_sends_the_models_array_and_records_the_fallback_that_served(fake):
    fake.chat_replies.append(json.loads(FIXTURE.read_text()))
    with collect_usage() as used:
        res = chat(PARTICLE, "p", key="k", effort="high")
    body = fake.requests[-1]["body"]
    assert body["models"] == [PARTICLE, V41, MIMO]
    assert "model" not in body  # the array alone states the order
    assert body["reasoning"] == {"effort": "high"}
    assert res.model_served == MIMO and res.usage.model == MIMO
    assert used[0].model == MIMO and used[0].cost == pytest.approx(0.00007392)
    (row,) = CallStore().calls()
    assert row["model_requested"] == PARTICLE and row["model_served"] == MIMO
    assert row["models_requested"] == body["models"] and row["fell_back"] is True


def test_first_choice_serving_is_not_a_fallback_even_with_a_dated_suffix(fake):
    fake.chat_replies.append(chat_reply("x", model=PARTICLE + "-20261101"))
    with effort_scope("low"):
        chat(PARTICLE, "p", key="k")
    assert "reasoning" not in fake.requests[-1]["body"]  # Low asks for no reasoning
    (row,) = CallStore().calls()
    assert row["fell_back"] is False


def test_explicit_params_win_and_unknown_models_pass_through(fake):
    chat(PARTICLE, "p", key="k", models=[PARTICLE], reasoning={"max_tokens": 64})
    body = fake.requests[-1]["body"]
    assert body["models"] == [PARTICLE] and body["model"] == PARTICLE and body["reasoning"] == {"max_tokens": 64}
    chat("vendor/model", "p", key="k")
    body = fake.requests[-1]["body"]
    assert body["model"] == "vendor/model" and "models" not in body and "reasoning" not in body
    assert "fell_back" not in CallStore().calls()[-1]


def test_engines_carry_the_effort_to_every_call(fake, monkeypatch):
    from services.orchestrator import engines

    monkeypatch.setattr(engines, "OR_CHAT", fake.url + "/chat/completions", raising=False)
    monkeypatch.setattr(engines, "_chat", _routed(engines._chat, fake.url))
    engines.decompose_engine(PARTICLE, api_key="k", effort="high")("plan this")
    assert fake.requests[-1]["body"]["reasoning"] == {"effort": "high"}
    engines.work_fn(PARTICLE, api_key="k", effort="low")({"placement": "cloud-OpenRouter", "prompt": "do"})
    assert "reasoning" not in fake.requests[-1]["body"]
    assert fake.requests[-1]["body"]["models"][0] == PARTICLE


def _routed(real, base):
    """engines._chat with the hard-coded OpenRouter URL pointed at the fake (the client reads the base from env)."""

    def call(url, model, prompt, key="", timeout=60.0):
        if url.startswith("https://openrouter.ai/"):
            from services.openrouter import chat as or_chat

            res = or_chat(model, prompt, key=key, timeout=timeout)
            return res.text, res.cost
        return real(url, model, prompt, key=key, timeout=timeout)

    return call


# --- decider -------------------------------------------------------------------------------------
def test_decider_chain_defaults_to_the_decider_then_jev(monkeypatch):
    monkeypatch.delenv("OC_DECIDER_MODEL", raising=False)
    monkeypatch.delenv("OC_DECIDER_FALLBACKS", raising=False)
    assert decide.decider_chain() == [("decider", decide.DECIDER_MODEL), ("jev", decide.JEV_MODEL)]


def test_decider_chain_reads_env_and_always_ends_in_jev(monkeypatch):
    monkeypatch.setenv("OC_DECIDER_MODEL", "vendor/decider-a")
    monkeypatch.setenv("OC_DECIDER_FALLBACKS", "vendor/decider-b, typesafe/jev-1.13 ,vendor/decider-a")
    assert decide.decider_chain() == [("decider", "vendor/decider-a"), ("decider-fallback-1", "vendor/decider-b"),
                                      ("jev", decide.JEV_MODEL)]


def test_router_walks_the_decider_chain_past_a_404(fake, monkeypatch, tmp_path):
    monkeypatch.setenv("OC_DECIDER_MODEL", "vendor/gone")
    monkeypatch.setenv("OC_DECIDER_FALLBACKS", "vendor/decider-b")
    good = {"answers": {"route": {"choice": "cloud", "confidence": 0.9}, "tier": {"choice": "cheap"}},
            "model": "vendor/decider-b", "usage": {"cost": 1e-6}}
    fake.chat_replies.extend([404, good])
    r = default_router(tmp_path / "ledger.jsonl", base_url=fake.url.removesuffix("/v1"), api_key="k")
    assert [e.__name__ for e in r.engines] == ["decider", "decider-fallback-1", "jev"]
    d = r.route("summarize this page")
    assert (d.engine, d.model_used) == ("decider-fallback-1", "vendor/decider-b")
    asked = [q["body"]["model"] for q in fake.requests]
    assert asked == ["vendor/gone", "vendor/decider-b"]


def test_raw_catalog_note_names_the_price_source():
    assert "2026-11-13" in RAW["_note"] and "particle-ai/particle-1" in RAW["_note"]
