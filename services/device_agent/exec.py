"""Execute one signed step on the device (plan P1 + P2 + P4).

P1 supports `echo`. P2 adds folder-scoped `fs_read`, `fs_write`, and `fs_list`
when a `GrantStore` (and `device_id`) is supplied, recording every grant check
(allowed or refused) to `audit` when provided. P4 adds `app_action` and
`browser_action` through `WispActuator`, enforcing the device's `app` /
`browser` capability and relaying confirmation prompts to the gate
`ApprovalQueue`.
"""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING, Any

from services.device.protocol import EnvelopeError, open_envelope

if TYPE_CHECKING:
    from services.device.grants import GrantStore
    from services.device.registry import DeviceRegistry
    from services.device_agent.wisp_actuator import WispActuator
    from services.gate.queue import ApprovalQueue

SUPPORTED = ("echo", "fs_read", "fs_write", "fs_list", "app_action", "browser_action")

_REQUIRED_MODE = {
    "fs_read": "read",
    "fs_list": "read",
    "fs_write": "write",
}

_REQUIRED_CAPABILITY = {
    "app_action": "app",
    "browser_action": "browser",
}


def _resolve_capabilities(
    step: dict[str, Any],
    *,
    device_id: str,
    capabilities: list[str] | tuple[str, ...] | set[str] | None,
    registry: DeviceRegistry | None,
) -> set[str]:
    if capabilities is not None:
        return {str(c) for c in capabilities}
    if registry is not None and device_id:
        dev = registry.get(device_id)
        if dev is not None:
            return set(dev.capabilities)
        return set()
    step_caps = step.get("capabilities") or step.get("device_capabilities")
    if isinstance(step_caps, (list, tuple, set)):
        return {str(c) for c in step_caps}
    return set()


def execute(
    envelope: dict,
    secret: str,
    *,
    now: float | None = None,
    seen: set[str] | None = None,
    grants: GrantStore | None = None,
    device_id: str | None = None,
    audit: Any | None = None,
    capabilities: list[str] | tuple[str, ...] | set[str] | None = None,
    registry: DeviceRegistry | None = None,
    wisp: WispActuator | None = None,
    approval_queue: ApprovalQueue | None = None,
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

    if kind in _REQUIRED_CAPABILITY:
        req_cap = _REQUIRED_CAPABILITY[kind]
        did = str(device_id or step.get("device_id") or "")
        caps = _resolve_capabilities(step, device_id=did, capabilities=capabilities, registry=registry)
        if req_cap not in caps:
            reason = f"missing_capability:{req_cap}"
            if audit is not None and hasattr(audit, "append"):
                audit.append(
                    {
                        "event": "capability_check",
                        "device_id": did,
                        "kind": kind,
                        "capability": req_cap,
                        "allowed": False,
                        "reason": reason,
                    }
                )
            return {"ok": False, "error": f"refused: {reason}", "reason": reason}

        if audit is not None and hasattr(audit, "append"):
            audit.append(
                {
                    "event": "capability_check",
                    "device_id": did,
                    "kind": kind,
                    "capability": req_cap,
                    "allowed": True,
                    "reason": "ok",
                }
            )

        if wisp is None:
            return {
                "ok": False,
                "error": f"refused: {kind} requires a WispActuator",
                "reason": "wisp_not_configured",
            }

        act_res = wisp.act(step)
        if act_res.get("requires_confirm"):
            action_desc = str(step.get("action") or step.get("task") or step.get("prompt") or "")
            consent_payload = {
                "kind": kind,
                "device_id": did,
                "app": str(step.get("app") or ("browser" if kind == "browser_action" else "")),
                "action": action_desc,
                "prompt_id": str(act_res.get("prompt_id") or ""),
                "prompt": str(act_res.get("prompt") or action_desc),
                "choices": list(act_res.get("choices") or ["yes", "no"]),
                "egress": f"device:{did or 'local'}:wisp",
                "model": "wisp-actuator",
            }
            approval_id: str | None = None
            if approval_queue is not None:
                approval_id = approval_queue.submit(
                    who=f"device/{did}" if did else "device",
                    what=f"{kind}: {consent_payload['prompt'] or consent_payload['action']}",
                    payload=consent_payload,
                    session=str(step.get("run") or ""),
                    ts=now,
                )
            if audit is not None and hasattr(audit, "append"):
                audit.append(
                    {
                        "event": "wisp_confirm_parked",
                        "device_id": did,
                        "kind": kind,
                        "approval_id": approval_id,
                        "prompt_id": consent_payload["prompt_id"],
                    }
                )
            return {
                "ok": False,
                "kind": kind,
                "status": "parked",
                "parked": True,
                "requires_confirm": True,
                "reason": "awaiting_confirmation",
                "approval_id": approval_id,
                "prompt_id": consent_payload["prompt_id"],
                "prompt": consent_payload["prompt"],
            }

        if not act_res.get("ok", False):
            err = str(act_res.get("error") or "wisp_actuation_failed")
            return {"ok": False, "kind": kind, "requires_confirm": False, "error": f"wisp_error: {err}"}

        return {
            "ok": True,
            "kind": kind,
            "status": "done",
            "requires_confirm": False,
            "result": str(act_res.get("result") or ""),
        }

    return {"ok": False, "error": f"unsupported step kind {kind!r}; supported: {list(SUPPORTED)}"}
