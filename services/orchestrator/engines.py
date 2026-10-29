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
# A run's cloud-sandbox steps share one microVM — the run workspace. Steps
# build on earlier artifacts (files, installs), and every step's approved
# egress hosts merge into the same human-granted policy on that VM.
_RUN_SANDBOXES: dict[str, dict] = {}


def _close_quiet(sb) -> None:
    try:
        sb.close()
    except Exception:
        pass


def _reap_run_sandboxes(now: float | None = None) -> None:
    now = time.time() if now is None else now
    for rid, ent in list(_RUN_SANDBOXES.items()):
        if now - ent["last"] > _RUN_SANDBOX_IDLE_S:
            _close_quiet(ent["sb"])
            del _RUN_SANDBOXES[rid]


atexit.register(lambda: [_close_quiet(e["sb"]) for e in _RUN_SANDBOXES.values()])


def _run_sandbox(plane, run_id: str, hosts: set[str]):
    """Sandbox for `run_id` covering `hosts`; reuse while the grant covers
    the step, recreate on a widened host set (grant is re-bound+audited)."""
    _reap_run_sandboxes()
    ent = _RUN_SANDBOXES.get(run_id)
    if ent is not None and hosts <= ent["allow"]:
        ent["last"] = time.time()
        return ent["sb"]
    allow = hosts | (ent["allow"] if ent else set())
    if ent is not None:
        _close_quiet(ent["sb"])
    sb = plane.make_sandbox(egress_allowlist=sorted(allow), timeout=_RUN_SANDBOX_TTL_S)
    _RUN_SANDBOXES[run_id] = {"sb": sb, "allow": allow, "last": time.time()}
    return sb


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
        res = _run_sandbox(plane, run_id, hosts).exec(f"echo {b64} | base64 -d | bash")
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
