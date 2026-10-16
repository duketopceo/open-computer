"""Signed step envelopes between the orchestrator and a device agent (plan P1).

Threat model: the device agent executes on the user's machine, so a step
it receives must provably come from the orchestrator and must not be
replayable. Each device has its own secret, minted at registration and
never written to the audit log.

  seal(step, secret, now)  -> envelope {step, ts, nonce, sig}
  open_envelope(env, secret, now, seen) -> step  (raises EnvelopeError)

sig = HMAC-SHA256(secret, canonical_json(step) | ts | nonce). The
timestamp window bounds clock skew; `seen` (a set of nonces the caller
persists) blocks replay inside the window.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets as _secrets
import time

WINDOW = 120.0  # seconds a sealed envelope stays valid


class EnvelopeError(ValueError):
    """Signature, window or replay check failed. The step must not run."""


def mint_secret() -> str:
    return _secrets.token_hex(32)


def _canon(step: dict) -> bytes:
    return json.dumps(step, sort_keys=True, separators=(",", ":")).encode()


def _sig(secret: str, step: dict, ts: float, nonce: str) -> str:
    msg = _canon(step) + f"|{ts:.3f}|{nonce}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def seal(step: dict, secret: str, now: float | None = None) -> dict:
    ts = round(time.time() if now is None else now, 3)
    nonce = _secrets.token_hex(16)
    return {"step": step, "ts": ts, "nonce": nonce, "sig": _sig(secret, step, ts, nonce)}


def open_envelope(
    env: dict, secret: str, now: float | None = None, seen: set[str] | None = None, window: float = WINDOW
) -> dict:
    """Verify and return the step. `seen` is mutated: the nonce is recorded on success."""
    try:
        step, ts, nonce, sig = env["step"], float(env["ts"]), str(env["nonce"]), str(env["sig"])
    except (KeyError, TypeError, ValueError) as e:
        raise EnvelopeError(f"malformed envelope: {e}") from e
    if not hmac.compare_digest(sig, _sig(secret, step, ts, nonce)):
        raise EnvelopeError("bad signature")
    t = time.time() if now is None else now
    if abs(t - ts) > window:
        raise EnvelopeError(f"outside window: |{t:.0f}-{ts:.0f}| > {window:.0f}s")
    if seen is not None:
        if nonce in seen:
            raise EnvelopeError("replayed nonce")
        seen.add(nonce)
    return step
