#!/usr/bin/env python3
"""Router bench — Phase 0 E2. Exact-match on corpus.jsonl per candidate.

Candidates
  pplx-decider  OpenRouter Decisions API (POST /api/alpha/decisions),
                model perplexity/pplx-decider-v1.1-27b. Hosted, ~$2e-6/call.
  jev           TypeSafe Jev via OpenRouter Decisions API, model
                typesafe/jev-1.13 (resolves to jev-1.13-20260917).
                ~$1.3e-5/call. (The spec'd local arm — qwen3-4b via
                jev-shim :8931 -> llama.cpp :8091 — was tried first; the
                llama.cpp service is OOM-killed by systemd-oomd under
                bench load, so the hosted arm is the operative one.)
  auto          openrouter/auto via chat completions. Two calls per
                fixture: (a) classification prompt -> parse route word,
                (b) bare utterance -> response.model = auto's routing
                decision, mapped to a tier via MODEL_TIER_MAP.
  subq          SubQ Snap via its Decisions API (POST
                https://api.subq.ai/v1/systemone), model
                subq-snap-preview. Same {state, questions} contract;
                input-token-only billing. Key from SUBQ_API_KEY.

Usage (bills OpenRouter calls to orchestral-eval-v3):
  orch python3 bench/router/run_bench.py [--candidate X] [--limit N]

Outputs results-<candidate>.jsonl and summary.json next to the corpus.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for services.openrouter
from services.openrouter import attribution as _attr  # noqa: E402


def _attribution(url):
    """App attribution headers on every OpenRouter call (never sent to other hosts)."""
    return _attr.headers() if "openrouter.ai" in url else {}


HERE = Path(__file__).parent
CORPUS = HERE / "corpus.jsonl"
OR_BASE = "https://openrouter.ai/api"
SUBQ_URL = "https://api.subq.ai/v1/systemone"
JEV_MODEL = "typesafe/jev-1.13"
DECIDER_MODEL = "perplexity/pplx-decider-v1.1-27b"
SUBQ_MODEL = "subq-snap-preview"
TIMEOUT = 90

ROUTES = {
    "launch": "open, start, or close an application and nothing else - if the request also says what to do inside it (a page, a click, 'and then'), that is 'act' instead",
    "tool": "a desktop/system action - window ops, workspace switch, type text, screenshot, notify, run a command, find files",
    "agent": "spawn a background agent for a coding, research, or multi-step task - phrases like 'agent', 'have an agent', 'spawn', 'delegate'",
    "learn": "the user wants the assistant to learn or remember how to do something - 'learn X', 'remember this', 'add a skill for'",
    "act": "a multi-step or in-app desktop task - do something on screen or inside an app: 'open X on the Y page', 'go to', 'find', 'click', 'and then', any sequence of actions",
    "dictation": "the user wants to dictate - type the words they speak into the focused app - 'dictate', 'type this', 'take dictation', 'write this down'",
    "answer": "the user is asking a question or chatting - respond in text, no desktop action",
    "clarify": "the request is too ambiguous to act on, or is a bare acknowledgment/correction with no actionable content",
}

TIERS = {
    "local": "trivial task a small local model handles fine - simple launches, clicks, short factual answers, dictation, clarifications",
    "cheap": "routine task needing a hosted flash-class model - explanations, multi-step app navigation, scoped agent work, searches",
    "mid": "hard task needing a strong mid-tier model - debugging, log analysis, broad research, design work, security review",
    "frontier": "exceptional task only the strongest frontier model can handle - reserved, almost never correct",
}

QUESTIONS = {
    "route": {"type": "choice",
              "instructions": "What kind of request is this? Pick the single best route.",
              "criteria": ROUTES},
    "tier": {"type": "choice",
             "instructions": "Which model tier should serve this task? Pick the cheapest tier that handles it well.",
             "criteria": TIERS},
}

# openrouter/auto reports the concrete model it picked in response.model.
# Map observed slugs -> tier. Fallback: price-band lookup by /models rates.
MODEL_TIER_MAP = {
    "deepseek/deepseek-v4.1-flash": "cheap",
    "deepseek/deepseek-chat-v3-0324": "cheap",
    "google/gemini-2.5-flash": "cheap",
    "google/gemini-2.5-flash-lite": "cheap",
    "openai/gpt-5-nano": "cheap",
    "openai/gpt-5-mini": "cheap",
    "openai/gpt-5": "mid",
    "x-ai/grok-4-fast": "mid",
    "x-ai/grok-4.7": "mid",
    "anthropic/claude-sonnet-4.5": "mid",
    "anthropic/claude-opus-4.5": "frontier",
    "openai/o3": "frontier",
}

PRICE_BANDS = [  # $/Mtok input ceilings, fallback when slug unmapped
    (0.30, "cheap"), (3.0, "mid"), (float("inf"), "frontier"),
]


def _key():
    return os.environ["OPENROUTER_API_KEY"]


def _post(url, body, auth=True, timeout=TIMEOUT, key=None):
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {key or _key()}"
    headers.update(_attribution(url))
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read()), time.time() - t0


def ask_decisions(url, model, utt, auth, key=None):
    payload = {"state": f'User request: "{utt}"', "questions": QUESTIONS}
    if model:
        payload["model"] = model
    d, ms = _post(url, payload, auth=auth, key=key)
    ans = d.get("answers", {})
    return {
        "route": (ans.get("route") or {}).get("choice"),
        "tier": (ans.get("tier") or {}).get("choice"),
        "confidence": (ans.get("route") or {}).get("confidence"),
        "model_used": d.get("model"),
        "cost": (d.get("usage") or {}).get("cost", 0.0),
        "input_tokens": (d.get("usage") or {}).get("input_tokens"),
        "ms": ms,
    }


def ask_auto_classify(utt):
    prompt = (
        "Classify this request into exactly one route. Reply with the "
        "route word only.\n\nRoutes:\n"
        + "\n".join(f"- {k}: {v}" for k, v in ROUTES.items())
        + f'\n\nRequest: "{utt}"\n\nRoute:')
    ms = 0.0
    cost = 0.0
    raw = ""
    model_used = None
    for _ in range(2):  # retry once: reasoning models can return empty
        d, t = _post(f"{OR_BASE}/v1/chat/completions", {
            "model": "openrouter/auto", "max_tokens": 256,
            "temperature": 0,
            "messages": [{"role": "user", "content": prompt}]})
        ms += t
        cost += (d.get("usage") or {}).get("cost", 0.0)
        model_used = d.get("model") or model_used
        raw = (d["choices"][0]["message"]["content"] or "").strip().lower()
        if raw:
            break
    m = re.search(r"\b(" + "|".join(ROUTES) + r")\b", raw)
    return {
        "route": m.group(1) if m else None,
        "tier": None,  # filled by ask_auto_route
        "confidence": None,
        "model_used": model_used,
        "cost": cost,
        "ms": ms,
        "raw": raw,
    }


_model_prices = {}


def _price_tier(slug):
    if not _model_prices:
        req = urllib.request.Request(
            f"{OR_BASE}/v1/models",
            headers={"Authorization": f"Bearer {_key()}", **_attribution(OR_BASE)})
        d = json.loads(urllib.request.urlopen(req, timeout=30).read())
        for m in d["data"]:
            try:
                _model_prices[m["id"]] = float(m["pricing"]["prompt"]) * 1e6
            except (KeyError, TypeError, ValueError):
                pass
    price = _model_prices.get(slug)
    if price is None:
        return None
    for ceil, tier in PRICE_BANDS:
        if price <= ceil:
            return tier
    return "frontier"


def model_to_tier(slug):
    return MODEL_TIER_MAP.get(slug) or _price_tier(slug)


def ask_auto_route(utt):
    """Bare task through openrouter/auto; response.model = its routing."""
    d, ms = _post(f"{OR_BASE}/v1/chat/completions", {
        "model": "openrouter/auto", "max_tokens": 1, "temperature": 0,
        "messages": [{"role": "user", "content": utt}]})
    slug = d.get("model")
    return slug, model_to_tier(slug), (d.get("usage") or {}).get("cost", 0.0), ms


def run_fixture(fx, cand):
    utt = fx["utterance"]
    try:
        if cand == "jev":
            r = ask_decisions(f"{OR_BASE}/alpha/decisions",
                              JEV_MODEL, utt, auth=True)
        elif cand == "pplx-decider":
            r = ask_decisions(f"{OR_BASE}/alpha/decisions",
                              DECIDER_MODEL, utt, auth=True)
        elif cand == "auto":
            r = ask_auto_classify(utt)
            slug, tier, cost2, ms2 = ask_auto_route(utt)
            r["tier"] = tier
            r["auto_route_model"] = slug
            r["cost"] += cost2
            r["ms"] += ms2
        elif cand == "subq":
            r = ask_decisions(SUBQ_URL, SUBQ_MODEL, utt, auth=True,
                              key=os.environ["SUBQ_API_KEY"])
        else:
            raise ValueError(cand)
        ok_route = r["route"] == fx["expected_route"]
        ok_tier = r["tier"] == fx["expected_tier"]
        return {**fx, "candidate": cand, "pred_route": r["route"],
                "pred_tier": r["tier"], "route_ok": ok_route,
                "tier_ok": ok_tier, "confidence": r.get("confidence"),
                "raw": r.get("raw"),
                "model_used": r.get("model_used"),
                "auto_route_model": r.get("auto_route_model"),
                "input_tokens": r.get("input_tokens"),
                "cost": r["cost"], "ms": round(r["ms"] * 1000),
                "error": None}
    except urllib.error.HTTPError as e:
        return {**fx, "candidate": cand, "pred_route": None,
                "pred_tier": None, "route_ok": False, "tier_ok": False,
                "confidence": None, "model_used": None, "raw": None,
                "auto_route_model": None, "input_tokens": None,
                "cost": 0.0, "ms": 0,
                "error": f"http {e.code}: {e.read()[:200]!r}"}
    except Exception as e:  # noqa: BLE001 - record, don't die
        return {**fx, "candidate": cand, "pred_route": None,
                "pred_tier": None, "route_ok": False, "tier_ok": False,
                "confidence": None, "model_used": None, "raw": None,
                "auto_route_model": None, "input_tokens": None,
                "cost": 0.0, "ms": 0,
                "error": f"{type(e).__name__}: {e}"}


def main():
    cand, limit = None, 0
    args = sys.argv[1:]
    while args:
        a = args.pop(0)
        if a == "--candidate":
            cand = args.pop(0)
        elif a == "--limit":
            limit = int(args.pop(0))
    fxs = [json.loads(l) for l in CORPUS.read_text().splitlines() if l.strip()]
    if limit:
        fxs = fxs[:limit]
    cands = [cand] if cand else ["jev", "pplx-decider", "auto", "subq"]
    summary = {}
    for c in cands:
        out = HERE / f"results-{c}.jsonl"
        workers = 6
        with ThreadPoolExecutor(max_workers=workers) as ex:
            rows = list(ex.map(lambda f: run_fixture(f, c), fxs))
        out.write_text("".join(json.dumps(r) + "\n" for r in rows))
        n = len(rows)
        errs = [r for r in rows if r["error"]]
        invalid = [r for r in rows if not r["error"]
                   and (r["pred_route"] is None or r["pred_tier"] is None)]
        summary[c] = {
            "n": n,
            "errors": len(errs),
            "invalid": len(invalid),
            "route_exact": sum(r["route_ok"] for r in rows),
            "tier_exact": sum(r["tier_ok"] for r in rows),
            "route_acc": round(sum(r["route_ok"] for r in rows) / n, 4),
            "tier_acc": round(sum(r["tier_ok"] for r in rows) / n, 4),
            "cost_usd": round(sum(r["cost"] for r in rows), 6),
            "p50_ms": sorted(r["ms"] for r in rows)[n // 2],
            "p95_ms": sorted(r["ms"] for r in rows)[int(n * .95)],
        }
        print(c, json.dumps(summary[c]))
    (HERE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


if __name__ == "__main__":
    main()
