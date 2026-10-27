"""Goal decomposition — goal text -> validated subtask DAG (U6).

The engine (an LLM call) is injectable; its output is schema-validated
and topology-checked before anything is dispatched. Malformed output
raises DecomposeError — a planner we can't parse is a halt, never a
guess.
"""

from __future__ import annotations

import re
from typing import Callable

import json as _json  # keep import surface obvious for the schema check

from services.placement import DEVICE_NEEDS


class DecomposeError(ValueError):
    """Engine output failed schema or topology validation."""


_FIELDS = {
    "id": str,
    "title": str,
    "prompt": str,
    "deps": list,
    "tier": str,
    "latency": str,
    "est_cost": float,
    "needs_sandbox": bool,
    "needs_egress": bool,
    "egress_hosts": list,
}

_TIERS = {"cheap", "mid", "frontier"}
_LATENCY = {"crit", "normal", "bulk"}


def _canon(raw: dict, i: int) -> dict:
    if not isinstance(raw, dict):
        raise DecomposeError(f"subtask {i} is not an object")
    title = raw.get("title")
    prompt = raw.get("prompt", title)
    if not isinstance(title, str) or not title.strip():
        raise DecomposeError(f"subtask {i} missing title")
    if not isinstance(prompt, str) or not prompt.strip():
        raise DecomposeError(f"subtask {i} missing prompt")
    sid = raw.get("id") or f"t{i + 1}"
    if not isinstance(sid, str):
        raise DecomposeError(f"subtask {i}: id must be a string")
    deps = raw.get("deps") or []
    if not all(isinstance(d, str) for d in deps):
        raise DecomposeError(f"subtask {sid}: deps must be strings")
    tier = raw.get("tier", "mid")
    latency = raw.get("latency", "normal")
    if tier not in _TIERS:
        raise DecomposeError(f"subtask {sid}: bad tier {tier!r}")
    if latency not in _LATENCY:
        raise DecomposeError(f"subtask {sid}: bad latency {latency!r}")
    out = {
        "id": sid,
        "title": title.strip(),
        "prompt": prompt.strip(),
        "deps": deps,
        "tier": tier,
        "latency": latency,
        "est_cost": float(raw.get("est_cost", 0.0) or 0.0),
        "needs_sandbox": bool(raw.get("needs_sandbox", False)),
        "needs_egress": bool(raw.get("needs_egress", False)),
        "egress_hosts": [
            h for h in (raw.get("egress_hosts") or []) if isinstance(h, str) and h.strip()
        ],
    }
    for k in (*DEVICE_NEEDS, "device_id", "capability", "kind", "app", "action"):
        if k in raw:
            out[k] = raw[k]
    return out


def _topo(tasks: list[dict]) -> None:
    """Deps must reference declared ids and form a DAG."""
    ids = {t["id"] for t in tasks}
    seen: dict[str, int] = {}  # 0=visiting 1=done

    def visit(sid: str, stack: list[str]) -> None:
        if sid not in ids:
            raise DecomposeError(f"dep {sid!r} not declared")
        if seen.get(sid) == 1:
            return
        if seen.get(sid) == 0:
            raise DecomposeError(f"dep cycle: {' -> '.join([*stack, sid])}")
        seen[sid] = 0
        for d in next(t for t in tasks if t["id"] == sid)["deps"]:
            visit(d, [*stack, sid])
        seen[sid] = 1

    for t in tasks:
        visit(t["id"], [])


def decompose(goal: str, engine: Callable[[str], str]) -> list[dict]:
    """goal -> subtask DAG. engine(goal) returns JSON (fences tolerated)."""
    raw = engine(
        "Decompose this goal into subtasks. Return ONLY a JSON array; each "
        'item: {"id","title","prompt","deps":[ids],"tier":cheap|mid|frontier,'
        '"latency":crit|normal|bulk,"est_cost":number,"needs_sandbox":bool,'
        '"needs_egress":bool,"egress_hosts":[domain,...]}. '
        "Set needs_egress when the step must reach the network (downloads, "
        "APIs); name the exact domains it needs in egress_hosts.\n"
        f"Goal: {goal}"
    )
    text = re.sub(r"^```(json)?\s*|\s*```$", "", raw.strip(), flags=re.M)
    try:
        items = _json.loads(text)
    except ValueError as e:
        raise DecomposeError(f"engine returned non-JSON: {e}") from e
    if not isinstance(items, list) or not items:
        raise DecomposeError("engine returned no subtasks")
    tasks = [_canon(t, i) for i, t in enumerate(items)]
    ids = [t["id"] for t in tasks]
    if len(set(ids)) != len(ids):
        raise DecomposeError("duplicate subtask ids")
    _topo(tasks)
    return tasks
