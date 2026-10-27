"""Local endpoint liveness — a sensor, not a policy (U5).

place() stays pure; place_live() wraps it with a cheap TCP probe of the
local model servers, cached for a few seconds so a dispatch wave doesn't
hammer dead ports. Fail-closed: a probe error means "down".
"""

from __future__ import annotations

import socket
import time

_ENDPOINTS = (("127.0.0.1", 8080), ("127.0.0.1", 11434))  # llama.cpp, Ollama
_TTL = 30.0
_cache: tuple[float, bool] = (0.0, True)


def local_endpoints_up(now: float | None = None) -> bool:
    """True if any local model port accepts a connection."""
    now = time.monotonic() if now is None else now
    ts, up = _cache
    if now - ts < _TTL:
        return up
    up = _probe()
    globals()["_cache"] = (now, up)
    return up


def _probe(timeout: float = 1.0) -> bool:
    for host, port in _ENDPOINTS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def place_live(subtask: dict, **kw):
    """place() with a live local-endpoint probe and the human-approved
    egress set injected (S5)."""
    from .rules import place

    kw.setdefault("local_up", local_endpoints_up())
    kw.setdefault("egress_approved", approved_egress())
    return place(subtask, **kw)


def approved_egress(policy_path=None) -> tuple[str, ...]:
    """Hosts a human granted via PolicyEngine.add_egress — read at dispatch
    time so a grant takes effect on the next step, not the next restart."""
    import os
    from pathlib import Path

    from services.execplane.policy import PolicyEngine

    if policy_path is None:
        root = Path(os.environ.get("OC_EXECPLANE_DIR", str(Path.home() / ".local/share/oc/execplane")))
        policy_path = root / "policy.json"
    return tuple(PolicyEngine(policy_path).approved_egress())
