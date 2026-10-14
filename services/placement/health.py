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
    """place() with a live local-endpoint probe injected."""
    from .rules import place

    kw.setdefault("local_up", local_endpoints_up())
    return place(subtask, **kw)
