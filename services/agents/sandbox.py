"""Sandbox launcher: run an ACP agent harness inside a microVM and bridge its stdio to the host.

execplane `exec` is request/response, so a small stdlib relay (relay.py) runs in the VM and the host talks to it
with short execs (see SandboxProc). One fresh VM per task; the repo is seeded from a per-job host workspace
and the VM's diff comes back as a checked patch (workspace.py). Everything the VM sends is untrusted input.
Every failure to set up is a WorkerUnavailable: the loop fails the step closed, never runs it on the host.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import select
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from services.execplane import PolicyDenied

from . import workspace as ws_mod

RELAY_SOURCE = Path(__file__).with_name("relay.py").read_text()
SEND_CHUNK = 48 * 1024
POLL_MAX = 256 * 1024
OUT_CAP = 64 * 1024 * 1024
ERR_CAP = 1024 * 1024
MAX_FAILS = 5
DIFF_OK = "OC-DIFF-COMPLETE"
KILL_BUDGET_S = 5.0
KILL_GRACE_S = 3.0
SEED_BUDGET_S = 120
COLLECT_BUDGET_S = 60


class SandboxError(RuntimeError):
    """The sandbox or its relay misbehaved or is unavailable."""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _write_file_cmd(path: str, data: bytes) -> str:
    return f"mkdir -p {shlex.quote(os.path.dirname(path))} && echo {_b64(data)} | base64 -d > {shlex.quote(path)}"


class _Stdin:
    def __init__(self, proc: "SandboxProc"):
        self._p = proc

    def write(self, data: bytes) -> int:
        self._p._send(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass  # EOF is the `close` marker (kill), sent by kill()


class SandboxProc:
    """Popen-shaped view of a harness running in a VM through the relay. Supplies every member AcpConnection
    touches: stdin.write/flush/close, stdout.readline/close, stderr.read1/close, wait(timeout), kill(), pid.
    kill() stops only the harness; the VM is closed by the launcher after the diff is collected."""

    def __init__(self, sb, relay_dir: str, *, poll_s: float = 0.1, idle_poll_s: float = 0.5):
        self._sb, self._dir = sb, relay_dir
        self._poll_s, self._idle_s = poll_s, idle_poll_s
        self._xlock = threading.Lock()  # SDK thread-safety is unknown: one exec at a time
        r_out, w_out = os.pipe()
        r_err, w_err = os.pipe()
        self.stdout = os.fdopen(r_out, "rb")
        self.stderr = os.fdopen(r_err, "rb")
        self._w_out, self._w_err = w_out, w_err
        os.set_blocking(w_out, False)  # a slow or dead reader must never wedge the poll thread
        os.set_blocking(w_err, False)
        self.stdin = _Stdin(self)
        self.pid = 0
        self._rc: int | None = None
        self._done = threading.Event()
        self._killed = False
        self._finish_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._out_off = self._err_off = 0
        self.note = ""  # why the relay ended abnormally (flood, dead relay, exec failures)

    # -- exec helpers ----------------------------------------------------
    def _exec(self, cmd: str):
        with self._xlock:
            return self._sb.exec(cmd)

    def _relay(self, *args) -> str:
        return f"python3 {shlex.quote(self._dir + '/relay.py')} " + " ".join(shlex.quote(str(a)) for a in args)

    # -- lifecycle ---------------------------------------------------------
    def start(self, argv: list[str], env: dict, cwd: str) -> None:
        d = shlex.quote(self._dir)
        launch = (
            f"{_write_file_cmd(self._dir + '/relay.py', RELAY_SOURCE.encode())} && "
            f"{_write_file_cmd(self._dir + '/env.json', json.dumps(env).encode())} && "
            f"(setsid nohup python3 {d}/relay.py serve {d} {shlex.quote(cwd)} "
            + " ".join(shlex.quote(a) for a in argv)
            + " >/dev/null 2>&1 </dev/null &)"
        )
        try:
            r = self._exec(launch)
        except Exception as e:  # noqa: BLE001
            raise SandboxError(f"relay launch failed: {type(e).__name__}: {e}") from None
        if r.returncode != 0:
            raise SandboxError(f"relay launch failed: rc={r.returncode}: {(r.stderr or '')[:200]}")
        for _ in range(30):  # handshake: the relay must survive the exec that started it
            st = self._poll_once(0)
            if st and st["ready"]:
                break
            time.sleep(0.1)
        else:
            raise SandboxError("relay did not survive exec")
        self._thread = threading.Thread(target=self._pump, daemon=True, name="sandbox-poll")
        self._thread.start()

    def _poll_once(self, limit: int = POLL_MAX) -> dict | None:
        try:
            r = self._exec(self._relay("poll", self._dir, self._out_off, self._err_off, limit))
            if r.returncode != 0 or len(r.stdout) > 2 * (limit * 4 // 3 + 4) + 512:
                return None  # untrusted size: checked before any parsing
            msg = json.loads(r.stdout.strip().splitlines()[-1])
            out = base64.b64decode(msg["out"], validate=True)
            err = base64.b64decode(msg["err"], validate=True)
            out_n, err_n = msg["out_n"], msg["err_n"]
            rc, alive, ready = msg["rc"], msg["alive"], msg["ready"]
            ok = (type(out_n) is int and type(err_n) is int and (rc is None or type(rc) is int)
                  and type(alive) is bool and type(ready) is bool
                  and out_n == self._out_off + len(out) and err_n == self._err_off + len(err)
                  and len(out) <= limit and len(err) <= limit)
            if not ok:
                return None
        except Exception:  # noqa: BLE001 - VM output is untrusted: anything odd is a failed poll
            return None
        return {"out": out, "err": err, "rc": rc, "alive": alive, "ready": ready}

    def _pump(self) -> None:
        fails = dead_polls = 0
        idle = self._poll_s
        try:
            while True:
                st = self._poll_once()
                if st is None:
                    fails += 1
                    if fails >= MAX_FAILS:
                        self.note = "sandbox exec failed repeatedly"
                        self._finish(255)
                        return
                    time.sleep(self._poll_s)
                    continue
                fails = 0
                if st["out"]:
                    self._out_off += len(st["out"])
                    if self._out_off > OUT_CAP:
                        self.note = "output flood"
                        self._finish(255)
                        return
                    if not self._write(self._w_out, st["out"]):
                        self.note = self.note or "reader gone"
                        self._finish(255)
                        return
                if st["err"]:
                    self._err_off += len(st["err"])
                    if self._err_off <= ERR_CAP and not self._write(self._w_err, st["err"]):
                        self.note = self.note or "reader gone"
                        self._finish(255)
                        return
                more = len(st["out"]) >= POLL_MAX or len(st["err"]) >= POLL_MAX
                if st["rc"] is not None and not more:
                    self._finish(st["rc"])
                    return
                if st["rc"] is None and not st["alive"]:
                    dead_polls += 1
                    if dead_polls >= MAX_FAILS:
                        self.note = "relay died"
                        self._finish(255)
                        return
                else:
                    dead_polls = 0
                if more:
                    continue
                idle = self._poll_s if (st["out"] or st["err"]) else min(self._idle_s, idle * 1.5)
                time.sleep(idle)
        except Exception:  # noqa: BLE001
            self.note = self.note or "poll thread error"
            self._finish(255)

    def _write(self, fd: int, data: bytes) -> bool:
        """Feed the ACP reader's pipe. False when it is gone: the reader died (bad line) or the connection was
        killed. The pipe is non-blocking so a reader that stopped reading can never wedge this thread."""
        view = memoryview(data)
        while view:
            if self._killed or self._done.is_set():
                return False
            try:
                n = os.write(fd, view)
            except BlockingIOError:
                select.select([], [fd], [], 0.25)
                continue
            except OSError:
                return False
            view = view[n:]
        return True

    def _finish(self, rc: int) -> None:
        """End the stream exactly once, from any thread: the pump when it finishes, or kill()/close() when the pump
        is wedged inside a hung exec. Closing the write ends is what unblocks the ACP reader."""
        with self._finish_lock:
            if self._done.is_set():
                return
            self._rc = rc
            self._done.set()  # before the close: _write refuses to touch an fd it may be about to lose
            for fd in (self._w_out, self._w_err):
                try:
                    os.close(fd)
                except OSError:
                    pass

    # -- Popen surface -----------------------------------------------------
    def _send(self, data: bytes) -> None:
        if self._done.is_set():
            raise OSError("relay is gone")
        for i in range(0, len(data), SEND_CHUNK):
            try:
                r = self._exec(self._relay("send", self._dir, _b64(data[i:i + SEND_CHUNK])))
            except Exception as e:  # noqa: BLE001
                raise OSError(f"sandbox send failed: {type(e).__name__}") from None
            if r.returncode != 0:
                raise OSError("sandbox send failed")

    def join(self, timeout: float) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def wait(self, timeout: float | None = None) -> int:
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("sandbox-harness", timeout)
        return int(self._rc if self._rc is not None else 255)

    def kill(self) -> None:
        if self._killed:
            return
        self._killed = True
        try:  # bypasses _xlock (a wedged poll holds it) and has a deadline
            _exec_with_deadline(self._sb, self._relay("close", self._dir), KILL_BUDGET_S)
        except Exception:  # noqa: BLE001 - the VM close is the backstop
            pass
        # Whatever happened to the close exec, the stream must end soon: a relay that never saw the marker would
        # otherwise leave the pump polling forever and the ACP reader (and so conn.close()) blocked.
        timer = threading.Timer(KILL_GRACE_S, self._finish, args=(255,))
        timer.daemon = True
        timer.start()

    def close(self) -> None:
        """Release the host side: end the stream and close the read ends (the VM close kills the relay)."""
        self._finish(255)
        for f in (self.stdout, self.stderr):
            try:
                f.close()
            except OSError:
                pass


def _exec_with_deadline(sb, cmd: str, seconds: float):
    """sb.exec in a helper thread: None when it does not return in time (the VM is closed right after)."""
    box: list = []

    def run():
        try:
            box.append(sb.exec(cmd))
        except Exception as e:  # noqa: BLE001
            box.append(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    if not box:
        return None
    if isinstance(box[0], Exception):
        raise box[0]
    return box[0]


@dataclass
class SandboxSession:
    proc: SandboxProc
    cwd: str
    ws: ws_mod.Workspace
    sb: object
    hosts: list[str]
    seed_files: int
    collect_budget_s: float = COLLECT_BUDGET_S
    base_sha: str = "HEAD"  # replaced by the seed commit; HEAD only if seeding recorded none
    diff_file: str = "/tmp/oc-diff.out"
    _closed: bool = field(default=False, repr=False)

    def finish(self) -> tuple[list[str], str]:
        """Collect the VM's diff and apply it to the job workspace, then close the VM. -> (changed, problem)."""
        changed, problem = [], ""
        try:
            self.proc.join(5)
            # Diff against the commit recorded at seed time (a harness that commits moves HEAD), to a file, not a
            # pipe (a failing git would hide behind base64's exit status), and only trust output ending in the marker.
            out = shlex.quote(self.diff_file)
            r = _exec_with_deadline(
                self.sb, f"cd {shlex.quote(self.cwd)} && git add -A && "
                f"git -c user.name=oc -c user.email=oc@localhost diff --cached --binary {self.base_sha} > {out} && "
                f"base64 -w0 {out} && echo && echo {DIFF_OK}",
                self.collect_budget_s)
            cap = ws_mod.PATCH_CAP_BYTES * 4 // 3 + 16  # base64 inflates by 4/3; checked before decoding
            if r is None:
                problem = "work lost: collecting the sandbox diff exceeded its time budget"
            elif r.returncode != 0:
                problem = f"work lost: could not collect the sandbox diff (rc={r.returncode})"
            elif len(r.stdout) > cap:
                problem = "sandbox patch rejected: over the size cap"
            elif r.stdout.rstrip().rsplit("\n", 1)[-1] != DIFF_OK:
                problem = "work lost: the sandbox diff command did not complete"
            else:
                try:
                    patch = base64.b64decode(r.stdout.rstrip().rsplit("\n", 2)[0].strip(), validate=True)
                except (binascii.Error, ValueError):
                    patch = b""
                    problem = "work lost: sandbox diff was not valid base64"
                if not problem:
                    changed = self.ws.apply_patch(patch)
        except ws_mod.WorkspaceError as e:
            problem = f"sandbox patch rejected: {e}"
        except Exception as e:  # noqa: BLE001 - VM expired, exec failed...
            problem = f"work lost: {type(e).__name__} while collecting the sandbox diff"
        finally:
            self.close()
        return changed, problem

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.proc.close()
            try:
                self.sb.close()
            except Exception:  # noqa: BLE001
                pass


class SandboxLauncher:
    def __init__(self, plane, root: Path | str, *, vm_dir: str = "/work", relay_dir: str = "/tmp/oc-relay",
                 home_dir: str = "/oc", env_extra: dict | None = None,
                 poll_s: float = 0.1, idle_poll_s: float = 0.5, seed_budget_s: int = SEED_BUDGET_S,
                 collect_budget_s: int = COLLECT_BUDGET_S):
        self.plane, self.root = plane, Path(root)
        self.vm_dir, self.relay_dir, self.home_dir = vm_dir, relay_dir, home_dir
        self.env_extra = dict(env_extra or {})  # static, non-secret harness env (tests point it at a fake agent)
        self.poll_s, self.idle_poll_s = poll_s, idle_poll_s
        self.seed_budget_s, self.collect_budget_s = seed_budget_s, collect_budget_s

    def open(self, *, job: str, src: str, hosts: list[str], argv: list[str], env: dict,
             files: dict[str, str], timeout_s: float, check_host: Callable[[str], str | None]) -> SandboxSession:
        """Boot, seed, write config files and start the relay. Raises SandboxError on any failure (caller
        turns it into WorkerUnavailable). The VM is closed on every failure path."""
        norm = []
        for h in hosts:
            n = check_host(h)
            if n is None:
                raise SandboxError(f"egress host {h!r} is not a plain public hostname")
            norm.append(n)
        norm = sorted(set(norm))
        try:
            wsp = ws_mod.Workspace(self.root, job)
            wsp.init(src)
            names = wsp.files()
            tar_b64, seed_files = wsp.tar_b64(names), len(names)
        except ws_mod.WorkspaceError as e:
            raise SandboxError(str(e)) from None
        if not self.plane.sandboxes_available():
            raise SandboxError("sandbox control plane unreachable")
        life = int(self.seed_budget_s + timeout_s + self.collect_budget_s)
        try:
            sb = self.plane.make_sandbox(egress_allowlist=norm, timeout=life)
        except ValueError as e:  # bind_egress: bad input
            raise SandboxError(f"sandbox refused: {e}") from None
        except PolicyDenied as e:
            raise SandboxError(f"sandbox refused: no execplane grant for host: {e}") from None
        except Exception as e:  # noqa: BLE001 - boot failure, SDK error...
            raise SandboxError(f"sandbox refused: {e}") from None
        proc = SandboxProc(sb, self.relay_dir, poll_s=self.poll_s, idle_poll_s=self.idle_poll_s)
        sess = SandboxSession(proc, self.vm_dir, wsp, sb, norm, seed_files, self.collect_budget_s)
        try:
            sess.base_sha = self._seed(sb, tar_b64)
            sess.diff_file = self.relay_dir + ".diff"
            for path, content in files.items():
                r = sb.exec(_write_file_cmd(path, content.encode()))
                if r.returncode != 0:
                    raise SandboxError("could not write the harness config in the sandbox")
            proc.start(argv, {**env, **self.env_extra}, self.vm_dir)
        except SandboxError:
            sess.close()
            raise
        except Exception as e:  # noqa: BLE001
            sess.close()
            raise SandboxError(f"sandbox setup failed: {type(e).__name__}: {e}") from None
        return sess

    def _seed(self, sb, tar_b64: str) -> str:
        w = shlex.quote(self.vm_dir)
        pre = sb.exec("git --version >/dev/null && python3 -V >/dev/null && command -v ori >/dev/null")
        if pre.returncode != 0:
            raise SandboxError("sandbox template lacks git, python3 or the ori harness")
        tmp = shlex.quote(self.relay_dir + ".seed.b64")
        r = sb.exec(f"mkdir -p {w} && : > {tmp}")
        if r.returncode != 0:
            raise SandboxError("could not prepare the sandbox work dir")
        deadline = time.monotonic() + self.seed_budget_s
        for i in range(0, len(tar_b64), SEND_CHUNK):
            if time.monotonic() > deadline:
                raise SandboxError("seeding the sandbox exceeded its time budget")
            r = sb.exec(f"printf %s {shlex.quote(tar_b64[i:i + SEND_CHUNK])} >> {tmp}")
            if r.returncode != 0:
                raise SandboxError("seeding the sandbox failed")
        r = sb.exec(
            f"base64 -d {tmp} | tar -xz -C {w} && rm -f {tmp} && cd {w} && git init -q && git add -A && "
            f"git -c user.name=oc -c user.email=oc@localhost commit -q --allow-empty -m base"
        )
        if r.returncode != 0:
            raise SandboxError(f"seeding the sandbox failed: {(r.stderr or '')[:200]}")
        r = sb.exec(f"cd {w} && git rev-parse HEAD")
        sha = (r.stdout or "").strip()
        if r.returncode != 0 or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
            raise SandboxError("could not record the sandbox base commit")
        return sha
