"""Dev trace: an opt-in, per-run event stream of everything a run decided and did.

Dev trace is a CONTENT-CAPTURE mode, like OC_OBS_CAPTURE_CONTENT=1 but for the runs that opt in: the stream holds
program text, command output, plan JSON and model reasoning. Every content field is redacted in full and only then
capped, so a cut can never leave a partial token behind.

The gate is decided once per run, at submit (`requested()`): `OC_DEV_TRACE=1`, or `dev.json` in the parent of
`OC_GOALS_DIR` holding `{"trace": true}`. A run that has it gets an `events.ndjson` in its run directory and every
producer emits only while that file exists (`tracer()`), so a mid-run toggle never splits a run and, with the file
absent, nothing is written, nothing is created and no provider request changes.

The stream is the documented goal event contract (docs/design/event-stream.md), written through `oc.events`.
Emitting never raises: a trace that cannot be written is logged and dropped, the run is unaffected.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .context import current
from .log import error_fields, get_logger
from .redact import redact_str

log = get_logger("devtrace")

STREAM = "events.ndjson"
SETTING = "dev.json"
RUN_ID_RE = re.compile(r"goal_[0-9a-f]{8}")
REASONING_CAP = 16_000  # characters, as services/orchestrator/thinking.py
TEXT_CAP = 16_000  # program text and plan JSON
TAIL_LINES = 50
TAIL_BYTES = 4096
_TRUNCATED = "\n… [truncated]"


# ---- the gate -------------------------------------------------------------------------------------------------


def goals_dir() -> Path:
    return Path(os.environ.get("OC_GOALS_DIR", str(Path.home() / ".local/share/oc/goals")))


def setting_path() -> Path:
    return goals_dir().parent / SETTING


def read_setting() -> bool:
    """`dev.json` says trace on. An absent, unreadable or invalid file is off. Never creates anything."""
    try:
        d = json.loads(setting_path().read_text())
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and d.get("trace") is True


def write_setting(on: bool) -> None:
    """Persist the console toggle: temp file + rename, mode 0600."""
    path = setting_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{SETTING}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps({"trace": bool(on)}).encode())
    finally:
        os.close(fd)
    os.replace(tmp, path)


def env_on() -> bool:
    return os.environ.get("OC_DEV_TRACE") == "1"


def requested() -> bool:
    """Should a run submitted now trace? Decided by the caller once, at submit."""
    return env_on() or read_setting()


def status() -> dict:
    return {"trace": read_setting(), "env": env_on(), "active": requested()}


def stream_path(run_id: str | None = None, run_dir: Path | str | None = None) -> Path | None:
    if run_dir is not None:
        return Path(run_dir) / STREAM
    if run_id and RUN_ID_RE.fullmatch(run_id):
        return goals_dir() / run_id / STREAM
    return None


def enable(run_dir: Path | str) -> None:
    """Turn the trace on for one run by creating its (empty) stream file."""
    fd = os.open(Path(run_dir) / STREAM, os.O_WRONLY | os.O_CREAT, 0o600)
    os.close(fd)


def enabled_for(run_id: str | None = None, run_dir: Path | str | None = None) -> bool:
    p = stream_path(run_id, run_dir)
    return p is not None and p.is_file()


# ---- content: redact in full, then cap ------------------------------------------------------------------------


def text(value, cap: int = TEXT_CAP) -> str:
    s = redact_str(str(value))
    return s if len(s) <= cap else s[:cap] + _TRUNCATED


def tail(value, lines: int = TAIL_LINES, nbytes: int = TAIL_BYTES) -> list[str]:
    """The end of some output: redacted whole first, then the last `nbytes` bytes, then the last `lines` lines."""
    if not value:
        return []
    s = redact_str(str(value))
    b = s.encode("utf-8", "replace")
    if len(b) > nbytes:
        s = b[-nbytes:].decode("utf-8", "ignore")
    return s.splitlines()[-lines:]


def entity(prefix: str, name: str) -> str:
    """An entity id the schema's `ref` pattern accepts: `step_` + subtask `t1` is `step_t1`."""
    body = re.sub(r"[^a-z0-9_]", "_", str(name).lower())
    return f"{prefix}_{body or 'x'}"


# ---- emitting -------------------------------------------------------------------------------------------------


class Tracer:
    """Best-effort writer onto one run's stream. Every method swallows its own failure."""

    def __init__(self, run_id: str, run_dir: Path, actor: str):
        from oc.events import Emitter, FileLog

        self.run_id = run_id
        self.log = FileLog(run_dir, STREAM)
        self.em = Emitter(self.log, run_id, actor={"name": actor, "kind": "system"})

    def emit(self, type_: str, **data) -> dict | None:
        try:
            return self.em.emit(type_, {k: v for k, v in data.items() if v is not None})
        except Exception as e:  # noqa: BLE001 - tracing must never change a run's outcome
            log.warning("dev trace event dropped", event_type=type_, **error_fields(e))
            return None

    def run(self, status: str, **data):
        return self.emit("run", status=status, **data)

    def step(self, subtask: str, title: str, status: str, **data):
        return self.emit("step", id=entity("step", subtask), title=title or subtask, status=status, **data)

    def error(self, id: str, code: str, message: str, subtask: str | None = None, **data):
        return self.emit("error", id=id, code=code, message=text(message, 2000) or code,
                         step=entity("step", subtask) if subtask else None, **data)

    def finished(self) -> bool:
        try:
            return self.log.finished(self.run_id)
        except Exception:  # noqa: BLE001
            return False


def tracer(run_id: str | None = None, run_dir: Path | str | None = None, actor: str | None = None) -> Tracer | None:
    """The run's tracer, or None when the run has no stream (dev trace off, or not a console run)."""
    ctx = current()
    run_id = run_id or ctx.get("run_id")
    path = stream_path(run_id, run_dir)
    if not run_id or path is None or not path.is_file():
        return None
    try:
        return Tracer(run_id, path.parent, actor or ctx.get("service") or "oc")
    except Exception as e:  # noqa: BLE001
        log.warning("dev trace unavailable", **error_fields(e))
        return None


def now() -> float:
    return round(time.time(), 3)
