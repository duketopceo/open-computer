"""The pluggable worker interface and its ACP implementation.

A worker takes a WorkerTask, drives a real agent harness to a stop, and returns a WorkerResult. OpenCode via
`ori opencode acp` is the first runtime; Codex/Claude Code are later AgentWorker implementations. Every tool
permission request goes through policy.classify: allow -> allow_once, refuse -> reject_once, ask -> the
owner approval hook (default deny). The sandbox launcher is a follow-up: a placement that needs a microVM
fails CLOSED here rather than running the harness on the owner's machine.
"""

from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from . import opencode, policy
from .acp import AcpConnection, AcpError

CONTRACT = (
    "You are a worker agent inside open-computer. Work only inside the current directory. Keep changes minimal "
    "and run the project's tests before you finish. Never read or write credential files. Do not push, deploy "
    "or open network connections unless asked; those need owner approval. Finish with a short factual summary."
)
_ENV_ALLOW = ("PATH", "HOME", "LANG", "TERM", "TMPDIR", "USER", "LOGNAME", "SHELL")
Approve = Callable[[str, dict], bool]  # (kind, detail) -> approved?
Emit = Callable[[str, dict], None]


class WorkerUnavailable(RuntimeError):
    """No harness could be started for this task; the loop fails closed on it."""


@dataclass
class WorkerTask:
    prompt: str
    cwd: str
    model: str
    placement: str = "local"  # "local" | "cloud-sandbox"
    timeout_s: float = 900.0
    est_cost: float = 0.0
    job: str = ""  # scopes the edit taint: a later job on the same worker and cwd starts clean


@dataclass
class WorkerResult:
    status: str  # done | failed | cancelled | timeout
    text: str = ""
    stop_reason: str = ""
    cost: float | None = None  # provider-reported when the harness sends it, else None
    detail: str = ""
    permissions: list[dict] = field(default_factory=list)


class AgentWorker(Protocol):
    name: str

    def run(self, task: WorkerTask, emit: Emit, approve: Approve) -> WorkerResult: ...


def money(value, default=None):
    """A cost-like number: finite and >= 0, else `default`. NaN/inf/negative/non-numeric never reach a budget."""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) and x >= 0 else default


def scrub_env(env: dict) -> dict:
    """Only a short allowlist reaches the harness: no API keys, tokens or session secrets leak in."""
    return {k: v for k, v in env.items() if k in _ENV_ALLOW or k.startswith("LC_")}


class AcpWorker:
    """Drive any ACP agent. `launch(root, env)` -> (argv, env); injected so tests use a fake agent."""

    def __init__(self, name: str, launch: Callable[[dict], tuple[list[str], dict]], home: Path | str):
        self.name = name
        self._launch = launch
        self.home = Path(home)
        self._tainted: set[tuple[str, str]] = set()  # (job, realpath cwd) that edited code

    def run(self, task: WorkerTask, emit: Emit, approve: Approve) -> WorkerResult:
        if task.placement == "cloud-sandbox":
            raise WorkerUnavailable("sandboxed agent workers are not wired yet; refusing to run on the host")
        if self.name == "opencode":
            opencode.write_config(self.home, CONTRACT)
        argv, env = self._launch(scrub_env(dict(os.environ)))
        chunks: list[str] = []
        perms: list[dict] = []
        cost: list[float | None] = [None]  # ACP usage_update.cost is a cumulative session total: keep the latest
        taint_key = (task.job, os.path.realpath(task.cwd))

        def on_update(_sid, upd):
            kind = upd.get("sessionUpdate")
            if kind == "agent_message_chunk":
                c = upd.get("content") or {}
                if c.get("type") == "text":
                    chunks.append(c.get("text", ""))
            elif kind in ("tool_call", "tool_call_update"):
                emit("tool", {"title": upd.get("title"), "status": upd.get("status"), "kind": upd.get("kind")})
            elif kind == "usage_update" and isinstance(upd.get("cost"), dict):
                latest = money(upd["cost"].get("amount"))
                if latest is not None:
                    cost[0] = latest

        def on_permission(_sid, tool_call, options, reply):
            title = tool_call.get("title")
            if str(tool_call.get("kind")) in policy.EDIT_KINDS and policy.touches_build_file(tool_call):
                self._tainted.add(taint_key)
            verdict = policy.classify(tool_call, task.cwd, taint_key in self._tainted)

            def answer(allow: bool, why: str):
                want = "allow_once" if allow else "reject_once"
                pick = next((o["optionId"] for o in options if o.get("kind") == want), None)
                perms.append({"tool": title, "verdict": why})
                emit("permission", {"tool": title, "verdict": why})
                reply(pick)

            if verdict != "ask":
                answer(verdict == "allow", verdict)
                return

            def ask():  # risky action: runs off the reader thread so approval may take a while
                try:
                    ok = bool(approve("tool", {"title": title, "kind": tool_call.get("kind"),
                                               "input": tool_call.get("rawInput")}))
                except Exception:  # noqa: BLE001 - fail closed
                    ok = False
                answer(ok, "approved" if ok else "denied")

            threading.Thread(target=ask, daemon=True).start()

        try:
            conn = AcpConnection(argv, env=env, cwd=task.cwd, on_update=on_update, on_permission=on_permission)
        except OSError as e:
            raise WorkerUnavailable(f"cannot start {argv[0]}: {e.strerror or e}") from None
        try:
            conn.initialize()
            sess = conn.new_session(task.cwd)
            sid = sess["sessionId"]  # KeyError/TypeError on a malformed reply is a failed result below
            self._pick_model(conn, sid, sess, task.model)
            emit("started", {"worker": self.name, "model": task.model})
            handle = conn.prompt(sid, task.prompt)
            try:
                stop = handle.wait(task.timeout_s)
            except TimeoutError:
                conn.cancel(sid)
                return WorkerResult("timeout", "".join(chunks), "timeout", cost[0], permissions=perms)
            status = "done" if stop == "end_turn" else "cancelled" if stop == "cancelled" else "failed"
            return WorkerResult(status, "".join(chunks), stop, cost[0], permissions=perms)
        except (AcpError, TimeoutError, KeyError, ValueError, TypeError) as e:
            tail = conn.stderr_tail.decode("utf8", "replace")[-300:]
            return WorkerResult("failed", "".join(chunks), "error", cost[0],
                                f"{type(e).__name__}: {e}: {tail}".strip(), perms)
        finally:
            conn.close()

    @staticmethod
    def _pick_model(conn, sid, sess, model):
        opts = [o for o in sess.get("configOptions") or [] if o.get("category") == "model"]
        if not opts:
            raise WorkerUnavailable("agent offers no model option; refusing to run on its default model")
        offered = {v.get("value") for v in opts[0].get("options") or []}
        if model not in offered:
            raise WorkerUnavailable(f"model {model!r} not offered by the agent")
        conn.set_config_option(sid, opts[0]["id"], model)


def opencode_worker(root: Path | str, ori: str = "ori") -> AcpWorker:
    return AcpWorker("opencode", lambda env: opencode.launch(root, env, ori=ori), root)
