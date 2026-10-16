"""Execute one signed step on the device. P1 knows exactly one step kind."""

from __future__ import annotations

import socket

from services.device.protocol import EnvelopeError, open_envelope

SUPPORTED = ("echo",)


def execute(envelope: dict, secret: str, *, now: float | None = None, seen: set[str] | None = None) -> dict:
    """Verify the envelope, run the step, return a result record. Never raises for a bad envelope:
    the refusal is itself the result so the orchestrator can audit it."""
    try:
        step = open_envelope(envelope, secret, now=now, seen=seen)
    except EnvelopeError as e:
        return {"ok": False, "error": f"refused: {e}"}
    kind = step.get("kind")
    if kind == "echo":
        return {"ok": True, "kind": "echo", "host": socket.gethostname(), "echo": step.get("text", "")}
    return {"ok": False, "error": f"unsupported step kind {kind!r}; P1 supports {list(SUPPORTED)}"}
