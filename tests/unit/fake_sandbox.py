"""A fake execplane for the sandbox launcher: `exec` runs real bash on this machine inside a temp "VM" dir.

Deliberately hostile like the real backends: a per-exec timeout, a command size cap, an output cap, injected
latency, cube-style raise-on-nonzero, and a mode where a process started by one exec is killed when it returns.
No network, no model: the harness is tests/fakes/acp_agent.py installed as `ori`.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from services.execplane import ExecResult, PolicyDenied, SandboxUnavailable

FAKE_AGENT = Path(__file__).resolve().parents[1] / "fakes" / "acp_agent.py"


def make_bindir(root: Path) -> Path:
    b = root / "bin"
    b.mkdir(parents=True, exist_ok=True)
    ori = b / "ori"
    ori.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE_AGENT} "$@"\n')
    ori.chmod(0o755)
    for tool in ("python3",):
        link = b / tool
        if not link.exists():
            link.symlink_to(sys.executable)
    return b


class FakeSandbox:
    def __init__(self, bindir: Path, relay_dir: str, *, exec_timeout=30.0, max_cmd=2_000_000, max_stdout=8_000_000,
                 latency=0.0, raise_on_nonzero=False, kill_bg_on_return=False, raise_when=None):
        self.bindir, self.relay_dir = bindir, relay_dir
        self.exec_timeout, self.max_cmd, self.max_stdout = exec_timeout, max_cmd, max_stdout
        self.latency, self.raise_on_nonzero = latency, raise_on_nonzero
        self.kill_bg_on_return, self.raise_when = kill_bg_on_return, raise_when
        self.closed = False
        self.cmds: list[str] = []

    def exec(self, cmd: str) -> ExecResult:
        if self.closed:
            raise SandboxUnavailable("sandbox closed")
        if len(cmd) > self.max_cmd:
            raise RuntimeError("command too long")
        if self.raise_when and self.raise_when(cmd):
            raise RuntimeError("sandbox expired")
        self.cmds.append(cmd)
        time.sleep(self.latency)
        env = {"PATH": f"{self.bindir}:/usr/bin:/bin", "HOME": str(self.bindir.parent), "LANG": "C.UTF-8"}
        r = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, timeout=self.exec_timeout)
        if self.kill_bg_on_return and "relay.py serve" in cmd:
            pidf = Path(self.relay_dir) / "relay.pid"
            for _ in range(100):
                if pidf.exists():
                    break
                time.sleep(0.02)
            try:
                pid = int(pidf.read_text())
                os.kill(pid, signal.SIGKILL)
            except (OSError, ValueError):
                pass
        out = r.stdout.decode("utf8", "replace")
        if len(out) > self.max_stdout:
            raise RuntimeError("exec output over the backend limit")
        if r.returncode != 0 and self.raise_on_nonzero:
            raise RuntimeError(f"exit status {r.returncode}")
        return ExecResult(r.returncode, out, r.stderr.decode("utf8", "replace"))

    def close(self) -> None:
        self.closed = True


class FakePlane:
    def __init__(self, bindir: Path, relay_dir: str, *, granted=("api.example.com",), available=True,
                 boot_fails=False, **sandbox_kw):
        self.bindir, self.relay_dir = bindir, relay_dir
        self.granted, self.available, self.boot_fails = set(granted), available, boot_fails
        self.sandbox_kw = sandbox_kw
        self.created: list[dict] = []
        self.sandboxes: list[FakeSandbox] = []

    def sandboxes_available(self) -> bool:
        return self.available

    def make_sandbox(self, egress_allowlist=None, secrets=None, boot_will_fail=False, template=None, timeout=300):
        hosts = list(egress_allowlist or [])
        for h in hosts:
            if h not in self.granted:
                raise PolicyDenied(f"egress host {h!r} was never approved by a human")
        self.created.append({"egress": hosts, "timeout": timeout, "secrets": secrets})
        sb = FakeSandbox(self.bindir, self.relay_dir, **self.sandbox_kw)
        if self.boot_fails:
            sb.closed = True  # every exec refuses, like a sandbox that never booted
        self.sandboxes.append(sb)
        return sb
