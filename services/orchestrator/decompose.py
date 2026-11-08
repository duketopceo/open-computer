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

from oc.obs import get_logger
from services.placement import DEVICE_NEEDS

log = get_logger("orchestrator")

# A dependent sees at most this many characters of each prior step's stdout.
DEP_OUTPUT_CAP = 1500


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
    "needs_web": bool,
    "egress_hosts": list,
}

_TIERS = {"cheap", "mid", "frontier"}
_LATENCY = {"crit", "normal", "bulk"}

# Near-miss values a planner model tends to emit, mapped onto the real enums.
_LATENCY_ALIASES = {
    "critical": "crit",
    "urgent": "crit",
    "high": "crit",
    "realtime": "crit",
    "low": "bulk",
    "background": "bulk",
    "batch": "bulk",
    "medium": "normal",
    "default": "normal",
}
_TIER_ALIASES = {"low": "cheap", "medium": "mid", "high": "frontier"}


def _enum(value, allowed: set, aliases: dict):
    """Lowercase, strip and alias a planner enum value; unknown values pass through unchanged."""
    if isinstance(value, str):
        v = value.strip().lower()
        v = aliases.get(v, v)
        if v in allowed:
            return v
    return value


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
    tier = _enum(raw.get("tier", "mid"), _TIERS, _TIER_ALIASES)
    latency = _enum(raw.get("latency", "normal"), _LATENCY, _LATENCY_ALIASES)
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
        "needs_web": bool(raw.get("needs_web", False)),
        "egress_hosts": [
            h.strip().lower() for h in (raw.get("egress_hosts") or []) if isinstance(h, str) and h.strip()
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


def _parse(raw: str) -> list[dict]:
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


def decompose(goal: str, engine: Callable[[str], str]) -> list[dict]:
    """goal -> subtask DAG. engine(goal) returns JSON (fences tolerated).

    A plan that fails validation gets one corrective retry with the validator
    message appended; a second failure raises."""
    prompt = (
        "Decompose this goal into subtasks. Return ONLY a JSON array; each "
        'item: {"id","title","prompt","deps":[ids],"tier":cheap|mid|frontier,'
        '"latency":crit|normal|bulk,"est_cost":number,"needs_sandbox":bool,'
        '"needs_egress":bool,"needs_web":bool,"egress_hosts":[domain,...]}. '
        "Set needs_web when the step must look something up on the web or read a page (the "
        "worker has a search and page-reading tool; no sandbox or egress_hosts needed for that). "
        "Set needs_egress when the step must reach the network (downloads, "
        "APIs); name the exact domains it needs in egress_hosts. Sandbox steps "
        "share one filesystem per run while the VM lives, but a step that adds "
        "egress hosts gets a fresh VM and earlier files are lost — never depend on "
        "files from a step with different egress_hosts; pass data between steps "
        f"through stdout, keeping each step's output under {DEP_OUTPUT_CAP} characters "
        f"(dependents receive only the first {DEP_OUTPUT_CAP}). Prompts must use "
        "relative paths or $HOME (never absolute paths like /workspace or /data).\n"
        f"Goal: {goal}"
    )
    raw = engine(prompt)
    try:
        return _parse(raw)
    except DecomposeError as first:
        log.warning("plan failed validation, retrying once", error=str(first))
        retry = (
            f"{prompt}\n\nYour previous reply was rejected: {first}. "
            "Reply again with ONLY the corrected JSON array, using exactly the allowed enum values."
        )
        return _parse(engine(retry))
