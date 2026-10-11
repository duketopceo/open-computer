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

import json
import os
import urllib.request
from typing import Callable


class SandboxWorkerUnavailable(RuntimeError):
    pass


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
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _chat(url: str, model: str, prompt: str, key: str = "", timeout: float = 60.0) -> tuple[str, float]:
    """OpenAI-compatible chat call -> (text, est_cost_usd)."""
    out = _post(
        url,
        {"model": model, "messages": [{"role": "user", "content": prompt}]},
        key=key,
        timeout=timeout,
    )
    text = out["choices"][0]["message"]["content"]
    usage = out.get("usage") or {}
    tokens = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
    return text, tokens / 1_000_000 * 0.30  # rough $/Mtok on cheap models


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
    try:
        text, _ = _chat(
            "http://127.0.0.1:8080/v1/chat/completions", model, prompt, timeout=90
        )
        return text
    except OSError:
        pass
    try:
        out = _post(
            "http://127.0.0.1:11434/api/generate",
            {"model": os.environ.get("OC_OLLAMA_MODEL", "ornith:latest"), "prompt": prompt, "stream": False},
            timeout=120,
        )
        return out["response"]
    except OSError as e:
        raise SandboxWorkerUnavailable(f"local model servers unreachable: {e}") from e


def work_fn(
    or_model: str = "z-ai/glm-5.3-flash",
    api_key: str | None = None,
) -> Callable[[dict], str]:
    """The dispatched-task executor. Placement is honored, not decorative."""
    key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")

    def run(task: dict) -> str:
        target = task.get("placement", "")
        if target.startswith("local"):
            return local_work(task)
        if target == "cloud-sandbox":
            raise SandboxWorkerUnavailable(
                "sandbox worker path not wired — needs U2 live execution"
            )
        # cloud-OpenRouter and anything unrecognized-but-cloud
        text, _ = _chat(
            "https://openrouter.ai/api/v1/chat/completions",
            or_model,
            task["prompt"],
            key=key,
            timeout=90,
        )
        return text

    return run
