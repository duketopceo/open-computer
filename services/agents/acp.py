"""Stdlib ACP client (protocol v1) for open-computer agent workers.

Newline-framed JSON-RPC 2.0 over a child's pipes: one reader thread, one stderr-drain
thread, request ids correlated with Event futures. Agent-to-client callbacks run ON THE
READER THREAD (must not block); the permission callback gets a reply() it may call later.
Advertises no fs/terminal capability.

Provenance: adapted from duketopceo/wisp PR #241 (wisp/acp.py), same author.
"""

from __future__ import annotations

import json
import subprocess
import threading

PROTOCOL_VERSION = 1
MAX_LINE = 16 * 1024 * 1024
STDERR_TAIL = 8192
CALL_TIMEOUT_S = 30.0


class AcpError(Exception):
    pass


class AcpClosed(AcpError):
    """The agent exited or the connection died."""


class AcpFramingError(AcpClosed):
    """Malformed or oversized line; the connection is dead."""


class AcpVersionError(AcpError):
    pass


class AcpRpcError(AcpError):
    def __init__(self, error):
        super().__init__(error.get("message") or str(error))
        self.code = error.get("code")
        self.data = error.get("data")


class _Future:
    def __init__(self):
        self.done = threading.Event()
        self.result = None
        self.error = None

    def set(self, result=None, error=None):
        if not self.done.is_set():
            self.result, self.error = result, error
            self.done.set()

    def wait(self, timeout=None):
        if not self.done.wait(timeout):
            raise TimeoutError("ACP call timed out")
        if self.error is not None:
            raise self.error
        return self.result


class PromptHandle:
    """Result of ``prompt``: ``wait()`` gives the stop reason."""

    def __init__(self, session_id, fut):
        self.session_id = session_id
        self._fut = fut

    def wait(self, timeout=None) -> str:
        return self._fut.wait(timeout)["stopReason"]


class AcpConnection:
    def __init__(self, argv, env=None, cwd=None, on_update=None,
                 on_permission=None, max_line=MAX_LINE,
                 start_new_session=False):
        self.on_update = on_update
        self.on_permission = on_permission
        self.max_line = max_line
        self.agent_capabilities: dict = {}
        self.stderr_tail = b""
        self.dead: AcpError | None = None
        self._lock = threading.Lock()
        self._wlock = threading.Lock()
        self._next_id = 0
        self._pending: dict = {}
        self._perms: dict = {}  # rpc id -> (session_id, reply fn)
        self._proc = subprocess.Popen(
            argv, env=env, cwd=cwd, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=start_new_session)
        self._threads = [
            threading.Thread(target=self._read_loop, daemon=True,
                             name="acp-reader"),
            threading.Thread(target=self._drain_stderr, daemon=True,
                             name="acp-stderr")]
        for t in self._threads:
            t.start()

    # -- transport ---------------------------------------------------

    def _send(self, obj):
        data = (json.dumps(obj) + "\n").encode()
        try:
            with self._wlock:
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
        except (OSError, ValueError):
            raise self.dead or AcpClosed("agent stdin closed") from None

    def _fail(self, exc):
        with self._lock:
            if self.dead is None:
                self.dead = exc
            pending, self._pending = self._pending, {}
        for fut in pending.values():
            fut.set(error=self.dead)
        try:
            self._proc.kill()
        except OSError:
            pass

    def _drain_stderr(self):
        try:
            while True:
                chunk = self._proc.stderr.read1(65536)
                if not chunk:
                    return
                self.stderr_tail = (self.stderr_tail + chunk)[-STDERR_TAIL:]
        except (OSError, ValueError):
            pass

    def _read_loop(self):
        out = self._proc.stdout
        try:
            while True:
                line = out.readline(self.max_line + 1)
                if not line:
                    rc = self._proc.wait()
                    self._fail(AcpClosed(f"agent exited ({rc})"))
                    return
                if len(line) > self.max_line and not line.endswith(b"\n"):
                    self._fail(AcpFramingError(
                        f"line over {self.max_line} bytes"))
                    return
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    self._fail(AcpFramingError("malformed JSON line"))
                    return
                if not isinstance(msg, dict):
                    self._fail(AcpFramingError("non-object message"))
                    return
                self._dispatch(msg)
        except (OSError, ValueError) as e:
            self._fail(AcpClosed(f"agent pipe error: {e}"))

    def _dispatch(self, msg):
        method, rid = msg.get("method"), msg.get("id")
        if method is None:  # response to one of our requests
            with self._lock:
                fut = self._pending.pop(rid, None)
            if fut is not None:
                if "error" in msg:
                    fut.set(error=AcpRpcError(msg["error"] or {}))
                else:
                    fut.set(result=msg.get("result") or {})
            return
        params = msg.get("params") or {}
        if method == "session/update":
            if self.on_update:
                try:
                    self.on_update(params.get("sessionId"),
                                   params.get("update") or {})
                except Exception:  # noqa: BLE001 — never kill the reader
                    pass
        elif method == "session/request_permission" and rid is not None:
            self._permission(rid, params)
        elif rid is not None:
            self._respond(rid, error={"code": -32601,
                                      "message": "method not found"})

    def _respond(self, rid, result=None, error=None):
        msg = {"jsonrpc": "2.0", "id": rid}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        try:
            self._send(msg)
        except AcpError:
            pass

    def _permission(self, rid, params):
        sid = params.get("sessionId")
        done = threading.Lock()

        def reply(option_id=None):
            """Answer once; no option id means cancelled."""
            if not done.acquire(blocking=False):
                return
            with self._lock:
                self._perms.pop(rid, None)
            outcome = ({"outcome": "selected", "optionId": option_id}
                       if option_id else {"outcome": "cancelled"})
            self._respond(rid, {"outcome": outcome})

        with self._lock:
            self._perms[rid] = (sid, reply)
        if self.on_permission is None:
            reply()
            return
        try:
            self.on_permission(sid, params.get("toolCall") or {},
                               params.get("options") or [], reply)
        except Exception:  # noqa: BLE001 — fail closed
            reply()

    def _call(self, method, params, timeout=CALL_TIMEOUT_S):
        return self._start(method, params).wait(timeout)

    def _start(self, method, params) -> _Future:
        fut = _Future()
        with self._lock:
            if self.dead is not None:
                raise self.dead
            self._next_id += 1
            rid = self._next_id
            self._pending[rid] = fut
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                        "params": params})
        except AcpError as e:
            fut.set(error=e)
        return fut

    # -- protocol ----------------------------------------------------

    def initialize(self) -> dict:
        res = self._call("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "clientCapabilities": {}})
        if res.get("protocolVersion") != PROTOCOL_VERSION:
            raise AcpVersionError(
                f"agent speaks protocol {res.get('protocolVersion')!r}, "
                f"need {PROTOCOL_VERSION}")
        self.agent_capabilities = res.get("agentCapabilities") or {}
        return res

    def new_session(self, cwd, mcp_servers=None) -> dict:
        return self._call("session/new", {
            "cwd": str(cwd), "mcpServers": mcp_servers or []})

    def load_session(self, session_id, cwd, mcp_servers=None) -> dict:
        return self._call("session/load", {
            "sessionId": session_id, "cwd": str(cwd),
            "mcpServers": mcp_servers or []})

    def set_config_option(self, session_id, config_id, value) -> dict:
        return self._call("session/set_config_option", {
            "sessionId": session_id, "configId": config_id,
            "value": value})

    def prompt(self, session_id, text) -> PromptHandle:
        fut = self._start("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": text}]})
        return PromptHandle(session_id, fut)

    def cancel(self, session_id):
        """Send ``session/cancel`` and answer this session's pending
        permission requests ``cancelled``."""
        try:
            self._send({"jsonrpc": "2.0", "method": "session/cancel",
                        "params": {"sessionId": session_id}})
        except AcpError:
            pass
        with self._lock:
            replies = [r for s, r in self._perms.values()
                       if s == session_id]
        for reply in replies:
            reply()

    @property
    def pid(self) -> int:
        return self._proc.pid

    def close(self):
        self._fail(AcpClosed("closed"))
        try:
            self._proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self._proc.wait(5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        for t in self._threads:
            t.join(5)
        for f in (self._proc.stdout, self._proc.stderr):
            try:
                f.close()
            except OSError:
                pass
