#!/usr/bin/env python3
"""Router bench leg 2 — quality-per-dollar through the real eval loop.

For each (router, task): the router picks a worker tier, mapped to a
concrete OpenRouter worker, and the task runs through orchestral's
Plan->Delegate->Assemble->Validate->Judge loop under a fixed
orchestrator. Output: loop-runs.jsonl — one row per (router, task) with
the pick, the run's pass/fail + cost, and quality-per-dollar.

Tier -> worker map (price ceiling = Grok 4.7, $1.6/$4.8):
  local    -> deepseek/deepseek-v4.1-flash  ($0.15/$0.60) cheapest hosted
              stand-in; no stable local worker endpoint exists for the
              full loop (llama.cpp is OOM-killed under load)
  cheap    -> deepseek/deepseek-v4.1-flash  ($0.15/$0.60)
  mid      -> openai/gpt-5.4-mini           ($0.25/$1.00)
  frontier -> x-ai/grok-4.7                 ($1.60/$4.80)

Usage: run from the orchestral repo, under `orch`:
  orch python3 run_loop_leg.py --runs-dir /tmp/routerbench-runs
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

OR_BASE = "https://openrouter.ai/api"
DECIDER_MODEL = "perplexity/pplx-decider-v1.1-27b"
JEV_MODEL = "typesafe/jev-1.13"

TIERS = {
    "local": "trivial task - a small local model or the cheapest hosted model handles it fine",
    "cheap": "routine task - a hosted flash-class model ($0.15/Mtok) handles it",
    "mid": "hard task - needs a strong mid-tier model ($0.25-1.00/Mtok)",
    "frontier": "exceptional task - only the strongest model ($1.6+/Mtok) can handle it; almost never correct",
}

TIER_WORKER = {
    "local": "deepseek/deepseek-v4.1-flash",
    "cheap": "deepseek/deepseek-v4.1-flash",
    "mid": "openai/gpt-5.4-mini",
    "frontier": "x-ai/grok-4.7",
}

ORCHESTRATOR = "deepseek/deepseek-v4-pro"  # fixed so only the worker pick varies

TASKS = [  # (task id, difficulty, one-line task brief for the router)
    ("code-fizzbuzz", "easy",
     "Write a single-function Python module implementing fizzbuzz(n) — "
     "one file, ~15 lines, unit-tested by the harness."),
    ("bugfix-lru-evict", "medium",
     "Debug and fix a buggy LRU cache eviction implementation — read the "
     "broken code, find the logic error, patch it, tests must pass."),
    ("multi-file-site", "hard",
     "Build a multi-page static site from a spec — several HTML/CSS files "
     "that must be internally consistent and match layout requirements."),
]


def _key():
    return os.environ["OPENROUTER_API_KEY"]


def _post(url, body, timeout=90):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {_key()}"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def pick_tier_decisions(model, brief):
    q = {"tier": {"type": "choice",
                  "instructions": "Which model tier should be assigned "
                                  "as the worker for this task?",
                  "criteria": TIERS}}
    d = _post(f"{OR_BASE}/alpha/decisions",
              {"model": model, "state": f"Task: {brief}",
               "questions": q})
    ans = d["answers"]["tier"]
    return ans["choice"], ans.get("confidence"), d.get("usage", {}).get("cost", 0)


def pick_auto(brief):
    """Bare task prompt through openrouter/auto; response.model = pick."""
    d = _post(f"{OR_BASE}/v1/chat/completions",
              {"model": "openrouter/auto", "max_tokens": 1,
               "messages": [{"role": "user", "content": brief}]})
    slug = d.get("model", "")
    return slug, d.get("usage", {}).get("cost", 0)


def pick_jev_shim(brief):  # optional, only if :8931 alive
    req = urllib.request.Request(
        "http://127.0.0.1:8931/", data=json.dumps({
            "state": f"Task: {brief}", "questions": {
                "tier": {"type": "choice", "instructions":
                         "Which model tier should be assigned as the "
                         "worker for this task?", "criteria": TIERS}}
        }).encode(), headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read())
    return d["answers"]["tier"]["choice"], \
        d["answers"]["tier"].get("confidence"), 0.0


def harness_run(task, worker, runs_dir):
    cmd = [".venv/bin/python", "harness.py",
           "--runs-dir", runs_dir, "run",
           "--task", task,
           "--orchestrator", ORCHESTRATOR,
           "--worker", worker,
           "--group", "router-bench",
           "--json"]
    env = dict(os.environ)
    p = subprocess.run(cmd, capture_output=True, text=True, env=env,
                       timeout=900)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"error": p.stdout[-300:] + p.stderr[-300:],
                "returncode": p.returncode}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-dir", default="/tmp/routerbench-runs")
    ap.add_argument("--tasks", default=",".join(t[0] for t in TASKS))
    args = ap.parse_args()
    tasks = [t for t in TASKS if t[0] in args.tasks.split(",")]

    picks = {}
    for cand in ("jev", "pplx-decider", "auto"):
        for tid, diff, brief in tasks:
            try:
                if cand == "auto":
                    slug, c = pick_auto(brief)
                    picks[(cand, tid)] = {
                        "pick_tier": None, "pick_model": slug,
                        "confidence": None, "router_cost": c}
                else:
                    model = JEV_MODEL if cand == "jev" else DECIDER_MODEL
                    tier, conf, c = pick_tier_decisions(model, brief)
                    picks[(cand, tid)] = {
                        "pick_tier": tier, "pick_model": TIER_WORKER[tier],
                        "confidence": conf, "router_cost": c}
            except Exception as e:  # noqa: BLE001
                picks[(cand, tid)] = {"error": f"{type(e).__name__}: {e}",
                                      "pick_tier": None, "pick_model": None,
                                      "confidence": None, "router_cost": 0}
            print(cand, tid, picks[(cand, tid)], file=sys.stderr)

    def work(item):
        (cand, tid), pick = item
        if not pick.get("pick_model"):
            return {"candidate": cand, "task": tid, **pick,
                    "run": {"error": "no pick"}}
        run = harness_run(tid, pick["pick_model"], args.runs_dir)
        return {"candidate": cand, "task": tid, **pick, "run": run}

    with ThreadPoolExecutor(max_workers=4) as ex:
        rows = list(ex.map(work, picks.items()))

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "loop-runs.jsonl")
    with open(out, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"\nwrote {len(rows)} rows -> {out}")
    for r in rows:
        run = r.get("run", {})
        print(f"{r['candidate']:>13} {r['task']:>20} "
              f"tier={r.get('pick_tier')} model={r.get('pick_model')} "
              f"pass={run.get('passes', run.get('status'))} "
              f"cost={run.get('cost_total_usd')}")


if __name__ == "__main__":
    main()
