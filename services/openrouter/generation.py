"""Reconcile local call rows against OpenRouter's generation-stats API (`GET /generation?id=gen-...`).

The stats are eventually consistent (a fresh id can 404), so this runs after the fact, never in the hot path.
Outcome per call: `reconciled` (cost and tokens agree), `mismatch` (they differ: treat as a bug report),
`missing` (OpenRouter has no record yet; retried on the next run). Rows are appended, never rewritten.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from . import attribution
from .client import base_url
from .store import CallStore

COST_ABS_TOL = 1e-6
COST_REL_TOL = 0.005


def fetch_generation(gen_id: str, key: str, *, attempts: int = 4, delay: float = 2.0, timeout: float = 15.0,
                     sleep: Callable[[float], None] = time.sleep) -> dict | None:
    """Return the generation's `data` dict, or None if it is not (yet) known. Retries 404/5xx with backoff."""
    url = f"{base_url()}/generation?id={urllib.parse.quote(gen_id)}"
    hdr = {"Authorization": f"Bearer {key}", "User-Agent": "open-computer/0.1", **attribution.headers()}
    for i in range(attempts):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=timeout) as r:
                d = json.loads(r.read())
            data = d.get("data") if isinstance(d, dict) else None
            if isinstance(data, dict):
                return data
        except urllib.error.HTTPError as e:
            if e.code not in (404, 429, 500, 502, 503, 504):
                raise
        except (OSError, ValueError):
            pass
        if i < attempts - 1:
            sleep(delay * (2**i))
    return None


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(COST_ABS_TOL, COST_REL_TOL * max(abs(a), abs(b)))


def compare(call: dict, stats: dict) -> dict:
    cost = stats.get("total_cost", stats.get("usage"))
    t_in, t_out = stats.get("tokens_prompt"), stats.get("tokens_completion")
    problems = []
    if isinstance(cost, (int, float)) and not _close(float(call.get("cost") or 0.0), float(cost)):
        problems.append("cost")
    if isinstance(t_in, int) and t_in != int(call.get("tokens_in") or 0):
        problems.append("tokens_in")
    if isinstance(t_out, int) and t_out != int(call.get("tokens_out") or 0):
        problems.append("tokens_out")
    return {
        "status": "mismatch" if problems else "reconciled",
        "mismatch_fields": problems,
        "stats_cost": cost,
        "cost_delta": (float(cost) - float(call.get("cost") or 0.0)) if isinstance(cost, (int, float)) else None,
        "stats_tokens_in": t_in, "stats_tokens_out": t_out,
        "stats_latency_ms": stats.get("latency"), "stats_generation_ms": stats.get("generation_time"),
        "stats_provider": stats.get("provider_name"), "stats_finish_reason": stats.get("finish_reason"),
        "stats_cancelled": stats.get("cancelled"), "stats_is_byok": stats.get("is_byok"),
        "stats_upstream_cost": stats.get("upstream_inference_cost"),
    }


def reconcile(store: CallStore, key: str, *, min_age: float = 30.0, limit: int = 200,
              fetch: Callable[..., dict | None] = fetch_generation, now: float | None = None) -> dict[str, int]:
    """Reconcile calls that have a generation id and no final result. Returns counts by outcome."""
    now = time.time() if now is None else now
    counts = {"reconciled": 0, "mismatch": 0, "missing": 0, "skipped": 0}
    todo = [c for c in store.calls()
            if c.get("status") == "ok" and c.get("generation_id")
            and c.get("reconcile_status") in ("unreconciled", "missing")
            and now - float(c.get("ts", 0)) >= min_age][:limit]
    for c in todo:
        try:
            stats = fetch(c["generation_id"], key)
        except Exception as e:  # auth/other HTTP errors: stop hammering, report as skipped
            counts["skipped"] += 1
            store.append({"event": "reconcile", "call_id": c["call_id"], "status": "missing", "error": str(e)[:200]})
            continue
        res = {"status": "missing"} if stats is None else compare(c, stats)
        store.append({"event": "reconcile", "call_id": c["call_id"], **res})
        counts[res["status"]] += 1
    return counts
