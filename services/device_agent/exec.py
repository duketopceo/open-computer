"""Execute one signed step on the device (plan P1 + P2).

P1 supports `echo`. P2 adds folder-scoped `fs_read`, `fs_write`, and `fs_list`
when a `GrantStore` (and `device_id`) is supplied, recording every grant check
(allowed or refused) to `audit` when provided.
"""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING, Any

from services.device.protocol import EnvelopeError, open_envelope

if TYPE_CHECKING:
    from services.device.grants import GrantStore

SUPPORTED = ("echo", "fs_read", "fs_write", "fs_list")

_REQUIRED_MODE = {
    "fs_read": "read",
    "fs_list": "read",
    "fs_write": "write",
}


def execute(
    envelope: dict,
    secret: str,
    *,
    now: float | None = None,
    seen: set[str] | None = None,
    grants: GrantStore | None = None,
    device_id: str | None = None,
    audit: Any | None = None,
) -> dict:
    """Verify the envelope, run the step, return a result record. Never raises for a bad envelope:
    the refusal is itself the result so the orchestrator can audit it."""
    try:
        step = open_envelope(envelope, secret, now=now, seen=seen)
    except EnvelopeError as e:
        return {"ok": False, "error": f"refused: {e}"}

    kind = step.get("kind")
    if kind == "echo":
        return {"ok": True, "kind": "echo", "host": socket.gethostname(), "echo": step.get("text", "")}

    if kind in _REQUIRED_MODE:
        req_mode = _REQUIRED_MODE[kind]
        target_path = step.get("path")
        did = device_id or step.get("device_id") or ""
        if grants is None or not did:
            if audit is not None and hasattr(audit, "append"):
                audit.append(
                    {
                        "event": "grant_check",
                        "device_id": did,
                        "path": str(target_path or ""),
                        "mode": req_mode,
                        "allowed": False,
                        "reason": "grants_not_configured",
                    }
                )
            return {"ok": False, "error": f"refused: {kind} requires an active GrantStore and device_id"}
        if not target_path:
            if audit is not None and hasattr(audit, "append"):
                audit.append(
                    {
                        "event": "grant_check",
                        "device_id": did,
                        "path": "",
                        "mode": req_mode,
                        "allowed": False,
                        "reason": "missing_path",
                    }
                )
            return {"ok": False, "error": f"refused: {kind} requires 'path'"}

        chk = grants.check(did, target_path, req_mode, now=now, audit=audit)
        if not chk.allowed or chk.resolved_path is None:
            return {"ok": False, "error": f"refused: {chk.reason}", "reason": chk.reason}

        resolved = chk.resolved_path
        try:
            if kind == "fs_read":
                content = resolved.read_text(encoding="utf-8")
                return {
                    "ok": True,
                    "kind": "fs_read",
                    "path": str(resolved),
                    "grant_id": chk.grant_id,
                    "content": content,
                }
            if kind == "fs_write":
                resolved.parent.mkdir(parents=True, exist_ok=True)
                data = str(step.get("content", ""))
                resolved.write_text(data, encoding="utf-8")
                return {
                    "ok": True,
                    "kind": "fs_write",
                    "path": str(resolved),
                    "grant_id": chk.grant_id,
                    "bytes_written": len(data.encode("utf-8")),
                }
            if kind == "fs_list":
                entries = sorted(p.name for p in resolved.iterdir())
                return {
                    "ok": True,
                    "kind": "fs_list",
                    "path": str(resolved),
                    "grant_id": chk.grant_id,
                    "entries": entries,
                }
        except OSError as exc:
            return {"ok": False, "error": f"io_error: {exc}"}

    return {"ok": False, "error": f"unsupported step kind {kind!r}; supported: {list(SUPPORTED)}"}
