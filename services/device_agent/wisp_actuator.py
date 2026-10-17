"""Linux app and browser control through wisp's `wispd` IPC socket (plan P4).

Speaks the `wispd` Unix domain socket JSON-line contract (`docs/IPC_CONTRACT.md`
in wisp): one newline-terminated JSON object per connection (`{"cmd": ...}`)
with a newline-terminated JSON reply (`{"ok": bool, ...}`).

Supports an injectable callable or socket transport for deterministic unit
tests without a live Wayland/Hyprland session.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any, Callable

DEFAULT_TIMEOUT = 10.0


def default_sock_path() -> Path:
    """Default `wispd` Unix domain socket path per Wisp IPC contract v2."""
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg) / "wisp" / "wispd.sock"
    return Path("/tmp/wisp/wispd.sock")


class WispActuator:
    """Client for driving local apps and browsers through `wispd` JSON-line IPC."""

    def __init__(
        self,
        sock_path: Path | str | None = None,
        *,
        transport: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.sock_path = Path(sock_path) if sock_path is not None else default_sock_path()
        self._transport = transport
        self.timeout = timeout

    def send(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """Send one JSON-line command to `wispd` and return the decoded reply dict."""
        if self._transport is not None:
            return dict(self._transport(cmd))

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(str(self.sock_path))
            payload = json.dumps(cmd, separators=(",", ":")).encode("utf-8") + b"\n"
            sock.sendall(payload)
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
        except OSError as exc:
            return {"ok": False, "error": f"wisp_socket_error: {exc}"}
        finally:
            try:
                sock.close()
            except OSError:
                pass

        raw = buf.decode("utf-8", errors="replace").strip()
        if not raw:
            return {"ok": False, "error": "wisp_socket_error: empty reply"}
        try:
            reply = json.loads(raw)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"wisp_socket_error: malformed json: {exc}"}
        return reply if isinstance(reply, dict) else {"ok": False, "error": "wisp_socket_error: non-object reply"}

    def status(self) -> dict[str, Any]:
        """Query `wispd` daemon status (`{"cmd": "status"}`)."""
        return self.send({"cmd": "status"})

    def cancel(self, name: str | None = None) -> dict[str, Any]:
        """Cancel a named background agent (`task_cancel`) or interrupt the active turn (`interrupt`)."""
        if name:
            return self.send({"cmd": "task_cancel", "name": name})
        return self.send({"cmd": "interrupt"})

    def choice(
        self,
        pick: str,
        *,
        prompt_id: str | None = None,
        index: int | None = None,
    ) -> dict[str, Any]:
        """Resolve a pending `wispd` confirmation or choice prompt."""
        cmd: dict[str, Any] = {"cmd": "choice", "pick": pick}
        if prompt_id is not None:
            cmd["prompt_id"] = prompt_id
        if index is not None:
            cmd["index"] = index
        return self.send(cmd)

    def act(self, step: dict[str, Any]) -> dict[str, Any]:
        """Execute an `app_action` or `browser_action` step via `wispd`."""
        kind = str(step.get("kind") or "app_action")
        if kind not in ("app_action", "browser_action"):
            return {"ok": False, "requires_confirm": False, "error": f"unsupported_wisp_kind:{kind}"}

        target = "browser" if kind == "browser_action" else "app"
        action_text = str(step.get("action") or step.get("task") or step.get("prompt") or "")
        app_name = str(step.get("app") or ("browser" if kind == "browser_action" else ""))

        req: dict[str, Any] = {
            "cmd": str(step.get("wisp_cmd") or "agent"),
            "kind": kind,
            "target": target,
            "app": app_name,
            "action": action_text,
            "task": action_text,
        }
        if "args" in step and isinstance(step["args"], dict):
            req["args"] = dict(step["args"])
        if "url" in step:
            req["url"] = step["url"]

        try:
            reply = self.send(req)
        except Exception as exc:
            return {"ok": False, "requires_confirm": False, "error": f"wisp_transport_error: {exc}"}

        confirm_block = reply.get("confirm") if isinstance(reply.get("confirm"), dict) else {}
        requires_confirm = bool(
            reply.get("requires_confirm") or reply.get("status") == "awaiting_choice" or confirm_block
        )
        if requires_confirm:
            prompt_id = str(reply.get("prompt_id") or confirm_block.get("prompt_id") or "")
            prompt = str(
                reply.get("prompt")
                or confirm_block.get("prompt")
                or action_text
                or f"Confirm {kind} on {app_name or target}"
            )
            choices = list(reply.get("choices") or ["yes", "no"])
            return {
                "ok": True,
                "kind": kind,
                "requires_confirm": True,
                "prompt_id": prompt_id,
                "prompt": prompt,
                "choices": choices,
                "result": str(reply.get("result") or ""),
                "reply": reply,
            }

        if not reply.get("ok", False):
            return {
                "ok": False,
                "kind": kind,
                "requires_confirm": False,
                "error": str(reply.get("error") or "wisp_actuation_failed"),
                "reply": reply,
            }

        return {
            "ok": True,
            "kind": kind,
            "requires_confirm": False,
            "result": str(reply.get("result") or ""),
            "reply": reply,
        }
