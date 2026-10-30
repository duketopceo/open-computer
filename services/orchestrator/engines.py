"""Real execution surfaces for the orchestrator — engines and workers.

decompose_engine: the planner LLM call (returns text; decompose()
validates it). work_fn: executes one dispatched task — and honors the
dispatch's placement for real:

  local-*          -> llama.cpp :8080 (OpenAI-compat), Ollama :11434 fallback
  cloud-OpenRouter -> OpenRouter chat completion
  cloud-sandbox    -> raises SandboxWorkerUnavailable — sandbox workers
                      are not wired yet (U2 live path); the task fails
                      CLOSED with an honest reason, never a fake result

Keys come from env, never files: OPENROUTER_API_KEY. Models default cheap;
the user decides when local is proven (U12 eval gate).
"""

from __future__ import annotations

import atexit
import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from services.openrouter import OpenRouterError, collect_usage, total_usage
from services.openrouter import chat as or_chat

from .runner import WorkResult


class WorkerUnavailable(RuntimeError):
    """An execution surface is down. `hint` is an operator remediation
    line — the audit row and the inspector render it verbatim."""

    def __init__(self, msg: str, hint: str = ""):
        super().__init__(msg)
        self.hint = hint


class SandboxWorkerUnavailable(WorkerUnavailable):
    pass


class LocalModelUnavailable(WorkerUnavailable):
    pass


OR_CHAT = "https://openrouter.ai/api/v1/chat/completions"

_plane_instance = None


def _plane():
    """Lazy execplane singleton — constructed per-process, probed per task.

    Backend is explicit: OC_EXECPLANE_BACKEND selects `cube` (default) or
    `space`; an unknown value fails at construction, not per task."""
    global _plane_instance
    if _plane_instance is None:
        from services.execplane import adapter as plane_adapter

        _plane_instance = plane_adapter(
            Path(os.environ.get("OC_EXECPLANE_DIR", str(Path.home() / ".local/share/oc/execplane"))),
            backend=os.environ.get("OC_EXECPLANE_BACKEND", "cube"),
        )
    return _plane_instance


_SCRIPT_PROMPT = (
    "You are executing a task inside an isolated Linux microVM; outbound network is denied "
    "except hosts the task explicitly allows. "
    "Write ONE self-contained bash script that performs the task and prints the result to stdout. "
    "Rules: output ONLY the script (no markdown fences, no commentary); coreutils and python3 are available; "
    "write files ONLY inside the current working directory (use relative paths — system paths "
    "outside the workdir are read-only); keep stdout under 4000 characters; exit nonzero on failure.\nTask: "
)


_RUN_SANDBOX_IDLE_S = 900.0
_RUN_SANDBOX_TTL_S = 1800
# Replace a run VM this long before the backend kills it (fixed from creation;
# commands do not extend it), so a step never lands on an expired VM.
_RUN_SANDBOX_TTL_MARGIN_S = 120.0
# A run's cloud-sandbox steps share one microVM — the run workspace. Steps
# build on earlier artifacts (files, installs), and every step's approved
# egress hosts merge into the same human-granted policy on that VM.
_RUN_SANDBOXES: dict[str, dict] = {}
# `_RUN_LOCK` guards only the registry's in-memory bookkeeping (lookup/store,
# busy counts, victim selection) and is never held across a backend create or
# close, so one hung call cannot stall other runs. `_RUN_GUARDS` serializes
# creation per run_id (per process; workers keep separate registries).
_RUN_LOCK = threading.RLock()
_RUN_GUARDS: dict[str, threading.Lock] = {}
_reap_timer: threading.Timer | None = None


def _close_quiet(sb) -> None:
    try:
        sb.close()
    except Exception:
        pass


def _pop_idle_locked(now: float) -> list[dict]:
    """Remove idle run VMs from the registry (caller holds `_RUN_LOCK`); never one with a command in flight."""
    gone = []
    for rid, ent in list(_RUN_SANDBOXES.items()):
        if ent.get("busy", 0) == 0 and now - ent["last"] > _RUN_SANDBOX_IDLE_S:
            gone.append(_RUN_SANDBOXES.pop(rid))
    return gone


def _reap_run_sandboxes(now: float | None = None) -> None:
    """Close idle run VMs; never one with a command in flight (busy > 0)."""
    now = time.time() if now is None else now
    with _RUN_LOCK:
        gone = _pop_idle_locked(now)
    for ent in gone:  # backend close happens outside the registry lock
        _close_quiet(ent["sb"])


def _reap_tick() -> None:
    global _reap_timer
    with _RUN_LOCK:
        _reap_timer = None
    _reap_run_sandboxes()
    _schedule_reap()


def _schedule_reap() -> None:
    """Arm a daemon timer so an idle VM is reclaimed without a new request."""
    global _reap_timer
    with _RUN_LOCK:
        if _reap_timer is None and _RUN_SANDBOXES:
            _reap_timer = threading.Timer(_RUN_SANDBOX_IDLE_S / 3, _reap_tick)
            _reap_timer.daemon = True
            _reap_timer.start()


def _close_all_run_sandboxes() -> None:
    with _RUN_LOCK:
        ents = list(_RUN_SANDBOXES.values())
        _RUN_SANDBOXES.clear()
    for e in ents:
        _close_quiet(e["sb"])


atexit.register(_close_all_run_sandboxes)


def _acquire_run_sandbox(plane, run_id: str, hosts: set[str]) -> dict:
    """Entry for `run_id` covering `hosts` with its busy count taken; reuse
    while the grant covers the step and the VM is not near its backend
    lifetime, recreate on a widened host set (grant is re-bound+audited) or an
    expiring VM. A replaced entry leaves the registry BEFORE the new create, so
    a failed create never leaves a closed handle behind; a replaced VM with a
    command in flight is retired and closed when its last command releases.
    Caller MUST `_release_run_sandbox` in a finally."""
    with _RUN_LOCK:
        guard = _RUN_GUARDS.setdefault(run_id, threading.Lock())
    with guard:  # serializes this run's create; other runs are unaffected
        now = time.time()
        with _RUN_LOCK:
            gone = _pop_idle_locked(now)
            ent = _RUN_SANDBOXES.get(run_id)
            expiring = ent is not None and ent["busy"] == 0 and now - ent["born"] > _RUN_SANDBOX_TTL_S - _RUN_SANDBOX_TTL_MARGIN_S
            if ent is not None and hosts <= ent["allow"] and not expiring:
                ent["busy"] += 1
                ent["last"] = now
                reuse, allow, old_close = ent, None, False
            else:
                reuse = None
                allow = hosts | (ent["allow"] if ent else set())
                old_close = False
                if ent is not None:
                    del _RUN_SANDBOXES[run_id]
                    if ent["busy"] > 0:
                        ent["retired"] = True  # a step is mid-command on it: close when the last one releases
                    else:
                        old_close = True
        for g in gone:
            _close_quiet(g["sb"])
        if reuse is not None:
            _schedule_reap()
            return reuse
        if old_close:
            _close_quiet(ent["sb"])
        sb = plane.make_sandbox(egress_allowlist=sorted(allow), timeout=_RUN_SANDBOX_TTL_S)
        t = time.time()
        new = {"sb": sb, "allow": allow, "born": t, "last": t, "busy": 1}
        with _RUN_LOCK:
            _RUN_SANDBOXES[run_id] = new
        _schedule_reap()
        return new


def _release_run_sandbox(ent: dict) -> None:
    with _RUN_LOCK:
        ent["busy"] = max(0, ent["busy"] - 1)
        ent["last"] = time.time()  # idle clock starts when the command ends
        close = bool(ent.get("retired")) and ent["busy"] == 0
    if close:  # replaced/evicted while a command ran
        _close_quiet(ent["sb"])


def _invalidate_run_sandbox(run_id: str, ent: dict) -> None:
    """The VM itself failed (exec raised): drop it so the next step boots a
    fresh one. Closed by the last `_release_run_sandbox` if still busy."""
    with _RUN_LOCK:
        if _RUN_SANDBOXES.get(run_id) is ent:
            del _RUN_SANDBOXES[run_id]
        ent["retired"] = True


def _run_sandbox(plane, run_id: str, hosts: set[str]):
    """Sandbox for `run_id` (no busy hold; prefer acquire/release)."""
    ent = _acquire_run_sandbox(plane, run_id, hosts)
    _release_run_sandbox(ent)
    return ent["sb"]


def sandbox_work(
    task: dict,
    model: str = "z-ai/glm-5.3-flash",
    api_key: str | None = None,
    plane=None,
) -> str:
    """cloud-sandbox placement: model writes a single-shot script, a
    microVM runs it under deny-all egress, stdout becomes the result.
    A step carrying a run id shares the run's workspace VM (S5 grants
    still gate every boot); one-off steps get a fresh VM that is always
    reclaimed. Unavailable control plane and nonzero exits fail CLOSED —
    never faked."""
    plane = plane or _plane()
    if not plane.sandboxes_available():
        backend = os.environ.get("OC_EXECPLANE_BACKEND", "cube")
        raise SandboxWorkerUnavailable(f"{backend} sandbox control plane unreachable")
    key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")
    script, _ = _chat(OR_CHAT, model, _SCRIPT_PROMPT + task["prompt"], key=key, timeout=90)
    script = re.sub(r"^```\w*\n?|\n?```$", "", script.strip()).strip()
    if not script:
        raise RuntimeError("script planner emitted empty output")
    # S5: only hosts a human approved ever reach the payload — dispatch
    # computes the effective set from the placement, empty = deny-all.
    run_id = str(task.get("run") or "")
    hosts = set(task.get("egress_hosts") or [])
    b64 = base64.b64encode(script.encode()).decode()
    if run_id:
        ent = _acquire_run_sandbox(plane, run_id, hosts)
        try:
            res = ent["sb"].exec(f"echo {b64} | base64 -d | bash")
        except Exception:
            _invalidate_run_sandbox(run_id, ent)  # VM-level failure (rc != 0 is a script failure, not this)
            raise
        finally:
            _release_run_sandbox(ent)
    else:
        sb = plane.make_sandbox(egress_allowlist=sorted(hosts), timeout=300)
        try:
            res = sb.exec(f"echo {b64} | base64 -d | bash")
        finally:
            sb.close()
    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()[:400]
        raise RuntimeError(f"sandbox exec rc={res.returncode}: {detail}")
    return res.stdout[:64_000]


def _post(url: str, payload: dict, key: str = "", timeout: float = 60.0) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {key}"} if key else {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"{url} -> HTTP {e.code}: {body or e.reason}") from e


def _chat(url: str, model: str, prompt: str, key: str = "", timeout: float = 60.0) -> tuple[str, float]:
    """OpenAI-compatible chat call -> (text, cost_usd).

    OpenRouter calls go through services.openrouter: attribution on the request, a full local call record, and
    the provider-reported cost (no price guess). Local endpoints are free: cost 0.0."""
    if url == OR_CHAT:
        if not key:
            raise WorkerUnavailable(
                "OPENROUTER_API_KEY is not set — cloud calls cannot run",
                hint="export a key in ~/.config/oc/opsview.env and restart oc-opsview",
            )
        try:
            res = or_chat(model, prompt, key=key, timeout=timeout)
        except OpenRouterError as e:
            raise RuntimeError(str(e)) from e
        return res.text, res.cost
    out = _post(url, {"model": model, "messages": [{"role": "user", "content": prompt}]}, key=key, timeout=timeout)
    return out["choices"][0]["message"]["content"], 0.0


def decompose_engine(
    model: str = "z-ai/glm-5.3-flash",
    api_key: str | None = None,
) -> Callable[[str], str]:
    """Planner: one OpenRouter call, returns raw text for decompose()."""
    key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")

    def engine(prompt: str) -> str:
        text, _ = _chat(
            "https://openrouter.ai/api/v1/chat/completions",
            model,
            prompt,
            key=key,
            timeout=60,
        )
        return text

    return engine


def local_work(task: dict) -> str:
    """Execute on this machine's local servers — GPU llama.cpp, then Ollama."""
    prompt = task["prompt"]
    model = os.environ.get("OC_LOCAL_MODEL", "qwen3-4b")
    attempts = []
    try:
        text, _ = _chat("http://127.0.0.1:8080/v1/chat/completions", model, prompt, timeout=90)
        return text
    except (OSError, RuntimeError) as e:
        attempts.append(f"llama.cpp :8080 — {e}")
    try:
        out = _post(
            "http://127.0.0.1:11434/api/generate",
            {"model": os.environ.get("OC_OLLAMA_MODEL", "ornith:latest"), "prompt": prompt, "stream": False},
            timeout=120,
        )
        return out["response"]
    except (OSError, RuntimeError) as e:
        attempts.append(f"ollama :11434 — {e}")
    raise LocalModelUnavailable(
        "local model endpoints unreachable: " + "; ".join(attempts),
        hint="start llama-local.service or ollama.service, or let placement demote to cloud",
    )


def work_fn(
    or_model: str = "z-ai/glm-5.3-flash",
    api_key: str | None = None,
) -> Callable[[dict], str]:
    """The dispatched-task executor. Placement is honored, not decorative."""
    key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")

    def run(task: dict):
        target = task.get("placement", "")
        if target.startswith("local"):
            return local_work(task)
        with collect_usage() as used:
            if target == "cloud-sandbox":
                out = sandbox_work(task, model=or_model, api_key=key)
            else:  # cloud-OpenRouter and anything unrecognized-but-cloud
                out, _ = _chat(OR_CHAT, or_model, task["prompt"], key=key, timeout=90)
        usage = total_usage(used)
        # Provider-reported spend goes to the run's cost ledger (the cap enforces on real dollars).
        return WorkResult(out, usage) if usage is not None else out

    return run
