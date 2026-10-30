"""The pluggable worker interface and its ACP implementation.

A worker takes a WorkerTask, drives a real agent harness to a stop, and returns a WorkerResult. OpenCode via
`ori opencode acp` is the first runtime; Codex/Claude Code are later AgentWorker implementations. Every tool
permission request goes through policy.classify: allow -> allow_once, refuse -> reject_once, ask -> the
owner approval hook (default deny). The sandbox launcher is a follow-up: a placement that needs a microVM
fails CLOSED here unless a SandboxLauncher is configured; then the same ACP session runs against a harness
inside a microVM (sandbox.py) and its diff lands in a per-job host workspace, never in the owner's repo.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from oc.obs import redact_str

from . import opencode, policy
from .acp import AcpConnection, AcpError
from .sandbox import SandboxError, SandboxLauncher

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
    egress_hosts: tuple[str, ...] = ()  # owner-approved hosts a cloud-sandbox VM may reach (deny-by-default otherwise)


@dataclass
class WorkerResult:
    status: str  # done | failed | cancelled | timeout
    text: str = ""
    stop_reason: str = ""
    cost: float | None = None  # provider-reported when the harness sends it, else None
    detail: str = ""
    permissions: list[dict] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)  # files a sandbox round changed in the job workspace
    workspace: str = ""


class AgentWorker(Protocol):
    name: str

    def run(self, task: WorkerTask, emit: Emit, approve: Approve) -> WorkerResult: ...


def money(value, default=None):
    """A cost-like number: finite and >= 0, else `default`. NaN/inf/negative/non-numeric never reach a budget."""
    if isinstance(value, bool):  # JSON true/false are not amounts
        return default
    try:
        x = float(value)
    except (TypeError, ValueError, OverflowError):  # OverflowError: an int beyond float range
        return default
    return x if math.isfinite(x) and x >= 0 else default


_HOST_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_BAD_SUFFIX = (".local", ".internal", ".localhost", ".localdomain", ".lan", ".home", ".corp")
# The harness environment inside a VM: the same allowlist idea, with static values (nothing from the host).
VM_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/oc/home", "LANG": "C.UTF-8", "TERM": "dumb",
          "TMPDIR": "/tmp"}


def valid_host(host) -> str | None:
    """The punycode, lowercase form of a plain public-looking hostname, else None. Refuses IP literals,
    localhost and local-only suffixes, wildcards, schemes, ports, paths and trailing dots: an approved
    'hostname' must never reach a service on the owner's machine or network."""
    if not isinstance(host, str) or not host or host != host.strip() or host.endswith("."):
        return None
    try:
        h = host.lower().encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = h.split(".")
    if len(h) > 253 or len(labels) < 2 or not all(_HOST_LABEL.match(x) for x in labels):
        return None
    if labels[-1].isdigit() or h.endswith(_BAD_SUFFIX):
        return None
    return h


def scrub_env(env: dict) -> dict:
    """Only a short allowlist reaches the harness: no API keys, tokens or session secrets leak in."""
    return {k: v for k, v in env.items() if k in _ENV_ALLOW or k.startswith("LC_")}


class AcpWorker:
    """Drive any ACP agent. `launch(root, env)` -> (argv, env); injected so tests use a fake agent."""

    def __init__(self, name: str, launch: Callable[[dict], tuple[list[str], dict]], home: Path | str,
                 sandbox: SandboxLauncher | None = None):
        self.name = name
        self._launch = launch
        self.home = Path(home)
        self.sandbox = sandbox
        self._tainted: set[tuple[str, str]] = set()  # (job, realpath cwd) that edited code

    def run(self, task: WorkerTask, emit: Emit, approve: Approve) -> WorkerResult:
        if task.placement == "cloud-sandbox":
            return self._run_sandbox(task, emit, approve)
        if self.name == "opencode":
            opencode.write_config(self.home, CONTRACT)
        argv, env = self._launch(scrub_env(dict(os.environ)))
        return self._drive(task, emit, approve, argv, env, task.cwd, None)

    def _run_sandbox(self, task: WorkerTask, emit: Emit, approve: Approve) -> WorkerResult:
        if self.sandbox is None:
            raise WorkerUnavailable("sandboxed agent workers are not configured; refusing to run on the host")
        if self.name != "opencode":
            raise WorkerUnavailable(f"no sandbox recipe for the {self.name} runtime")
        vm_root = self.sandbox.home_dir
        argv, env = opencode.launch(vm_root, {**VM_ENV, "HOME": f"{vm_root}/home"})
        files = {f"{vm_root}/opencode-home/config/opencode/opencode.json":
                 json.dumps(opencode.build_config(CONTRACT), indent=2) + "\n"}
        try:
            sess = self.sandbox.open(job=task.job, src=task.cwd, hosts=list(task.egress_hosts), argv=argv, env=env,
                                     files=files, timeout_s=task.timeout_s, check_host=valid_host)
        except SandboxError as e:
            raise WorkerUnavailable(str(e)) from None
        emit("sandbox", {"hosts": sess.hosts, "files": sess.seed_files, "workspace": str(sess.ws.path)})
        res = None
        try:
            res = self._drive(task, emit, approve, argv, env, sess.cwd, sess.proc)
        finally:  # collect after the connection (and relay) closed, then always close the VM
            changed, problem = sess.finish()
            if res is not None:
                res.changed, res.workspace = changed, str(sess.ws.path)
                if problem:
                    res.status = "failed"
                    res.detail = redact_str(f"{problem}; {res.detail}".strip("; "))
                elif sess.proc.note:
                    res.detail = redact_str(f"{sess.proc.note}; {res.detail}".strip("; "))
        return res

    def _drive(self, task: WorkerTask, emit: Emit, approve: Approve, argv: list[str], env: dict, cwd: str,
               proc) -> WorkerResult:
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
                if latest is not None:  # cumulative total: never let a later, smaller report erase spend
                    cost[0] = latest if cost[0] is None else max(cost[0], latest)

        def on_permission(_sid, tool_call, options, reply):
            title = tool_call.get("title")
            if str(tool_call.get("kind")) in policy.EDIT_KINDS and policy.touches_build_file(tool_call):
                self._tainted.add(taint_key)
            verdict = policy.classify(tool_call, cwd, taint_key in self._tainted)

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
            conn = AcpConnection(argv, env=env, cwd=cwd, on_update=on_update, on_permission=on_permission, proc=proc)
        except OSError as e:
            raise WorkerUnavailable(f"cannot start {argv[0]}: {e.strerror or e}") from None
        try:
            conn.initialize()
            sess = conn.new_session(cwd)
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
            # redact BEFORE cutting: a slice must never leave half a secret that no pattern matches
            tail = redact_str(conn.stderr_tail.decode("utf8", "replace"))[-300:]
            return WorkerResult("failed", "".join(chunks), "error", cost[0],
                                redact_str(f"{type(e).__name__}: {e}") + f": {tail}", perms)
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


def opencode_worker(root: Path | str, ori: str = "ori", sandbox: SandboxLauncher | None = None) -> AcpWorker:
    return AcpWorker("opencode", lambda env: opencode.launch(root, env, ori=ori), root, sandbox)
