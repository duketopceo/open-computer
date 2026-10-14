"""Capability inventory behind /skills — what a run can actually invoke.

Each row is a real path in the tree with live health where one exists:
a TCP probe for local servers, key-presence for cloud, env config for
the sandbox, a socket to Temporal for the durable loop. Nothing here is
aspirational — if it isn't wired, it isn't listed.
"""

from __future__ import annotations

import os
import socket


def _up(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def collect() -> list[dict]:
    """[{name, kind, status, detail}] — status is a normative word."""
    or_key = bool(os.environ.get("OPENROUTER_API_KEY"))
    cube = bool(os.environ.get("CUBE_API_URL"))
    temporal = _up("127.0.0.1", 7233)
    llama = _up("127.0.0.1", 8080)
    ollama = _up("127.0.0.1", 11434)
    cloud_state = "ready" if or_key else "not configured"
    return [
        {
            "name": "OpenRouter cloud model",
            "kind": "engine · cloud",
            "status": cloud_state,
            "detail": "cloud-OpenRouter · router budgeted"
            + ("" if or_key else " — set OPENROUTER_API_KEY"),
        },
        {
            "name": "llama.cpp local model",
            "kind": "engine · local",
            "status": "ready" if llama else "down",
            "detail": "local-llamacpp · 127.0.0.1:8080 — GPU, private",
        },
        {
            "name": "Ollama local model",
            "kind": "engine · local",
            "status": "ready" if ollama else "down",
            "detail": "local-ollama · 127.0.0.1:11434 — CPU, private",
        },
        {
            "name": "Goal decomposer",
            "kind": "engine · cloud",
            "status": cloud_state,
            "detail": "splits a goal into a subtask DAG before anything runs",
        },
        {
            "name": "Sandbox worker",
            "kind": "worker · isolated",
            "status": "ready" if cube else "not configured",
            "detail": "cloud-sandbox · CubeSandbox microVM"
            + ("" if cube else " — set CUBE_API_URL"),
        },
        {
            "name": "Temporal drive loop",
            "kind": "durability",
            "status": "ready" if temporal else "down",
            "detail": "crash-resume, retries, schedules · 127.0.0.1:7233",
        },
        {
            "name": "Escalation gate",
            "kind": "safety",
            "status": "active",
            "detail": "sensitive or spendy work waits on a human decision",
        },
        {
            "name": "Spend caps",
            "kind": "safety",
            "status": "active",
            "detail": "per-task and daily budgets — over-budget demotes, never silently spends",
        },
        {
            "name": "Fail-closed placement",
            "kind": "safety",
            "status": "active",
            "detail": "PII stays local; unavailable workers fail honestly, no fabrication",
        },
    ]
