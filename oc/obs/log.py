"""Structured logging: JSON lines, levels, a debug switch, correlation ids and redaction built in.

    from oc.obs import get_logger
    log = get_logger("router")
    log.info("route decided", route="cloud", tier="mid", cost=0.0)     # fields are keyword args

Config (all optional; defaults work on a fresh checkout and write nothing to disk):
  OC_LOG_LEVEL            DEBUG|INFO|WARNING|ERROR (default INFO)
  OC_DEBUG=1              shorthand for DEBUG everywhere
  OC_LOG_LEVEL_<SERVICE>  per-service override, e.g. OC_LOG_LEVEL_ROUTER=DEBUG
  OC_LOG_DIR              if set, also write rotating NDJSON to <dir>/oc.log
  OC_LOG_MAX_BYTES        rotation size (default 20 MB), OC_LOG_BACKUPS (default 5)
  OC_LOG_STDERR=0         silence the stderr sink
  OC_OBS_CAPTURE_CONTENT=1  allow prompt/argument text in logs (default: metadata only; still redacted)

A logging failure never reaches the caller; it increments `failures()`.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import context
from .redact import redact

_ROOT = "oc"
_failures = 0
_configured_for: tuple | None = None
_RESERVED = {"msg", "args", "levelname", "levelno", "pathname", "filename", "module", "exc_info", "exc_text",
             "stack_info", "lineno", "funcName", "created", "msecs", "relativeCreated", "thread", "threadName",
             "processName", "process", "name", "taskName", "message", "fields"}


def failures() -> int:
    return _failures


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "on", "debug"}


def capture_content() -> bool:
    """True only with OC_OBS_CAPTURE_CONTENT=1: the single switch that lets prompt/completion/argument text
    into local logs and records (still redacted). Off by default; every call site asks here."""
    return (os.environ.get("OC_OBS_CAPTURE_CONTENT") or "").strip().lower() in {"1", "true", "yes", "on"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        service = record.name.split(".", 1)[1] if "." in record.name else record.name
        ctx = context.current()
        ctx.pop("service", None)  # the logger names the service that spoke; a bound `service` is the caller's
        row = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": service,
            "msg": record.getMessage(),
            **ctx,
            **getattr(record, "fields", {}),
        }
        if row.get("run_id"):  # a line that names its run (a service log with an explicit id) joins the same spine
            row.setdefault("trace_id", context.trace_id_for(str(row["run_id"])))
            row.setdefault("chat_id", row["run_id"])
        if record.exc_info:
            row["exc"] = self.formatException(record.exc_info)
        return json.dumps(redact(row), ensure_ascii=False, default=str, separators=(",", ":"))


class _Stderr(logging.StreamHandler):
    """Resolves sys.stderr at emit time so redirected/captured stderr works."""

    def __init__(self):
        super().__init__(sys.stderr)

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, _v):
        pass


def _count_failure(self, record):  # noqa: ARG001 - logging.Handler.handleError signature
    global _failures
    _failures += 1


def _parse_level(name: str | None) -> int | None:
    lv = logging.getLevelName((name or "").strip().upper())
    return lv if isinstance(lv, int) else None


def _level(service: str | None) -> int:
    if _truthy(os.environ.get("OC_DEBUG")):
        return logging.DEBUG
    if service:
        per = _parse_level(os.environ.get(f"OC_LOG_LEVEL_{service.upper()}"))
        if per is not None:
            return per
    return _parse_level(os.environ.get("OC_LOG_LEVEL")) or logging.INFO


def configure(force: bool = False) -> None:
    """(Re)build handlers from the environment. Called lazily by get_logger; safe to call again."""
    global _configured_for
    key = tuple(os.environ.get(k) for k in ("OC_LOG_DIR", "OC_LOG_STDERR", "OC_LOG_MAX_BYTES", "OC_LOG_BACKUPS"))
    if not force and _configured_for == key:
        return
    root = logging.getLogger(_ROOT)
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    root.propagate = False
    root.setLevel(logging.DEBUG)  # handlers/loggers below gate by level
    fmt = JsonFormatter()
    handlers: list[logging.Handler] = []
    if os.environ.get("OC_LOG_STDERR", "1") != "0":
        handlers.append(_Stderr())
    d = os.environ.get("OC_LOG_DIR")
    if d:
        try:
            Path(d).mkdir(parents=True, exist_ok=True)
            handlers.append(
                logging.handlers.RotatingFileHandler(
                    Path(d) / "oc.log",
                    maxBytes=int(os.environ.get("OC_LOG_MAX_BYTES", 20 * 1024 * 1024)),
                    backupCount=int(os.environ.get("OC_LOG_BACKUPS", 5)),
                    encoding="utf-8",
                )
            )
        except OSError:
            _count_failure(None, None)
    for h in handlers:
        h.setFormatter(fmt)
        h.handleError = lambda rec, _h=h: _count_failure(_h, rec)  # type: ignore[method-assign]
        root.addHandler(h)
    _configured_for = key


class Logger:
    def __init__(self, service: str):
        self.service = service
        self._log = logging.getLogger(f"{_ROOT}.{service}")

    def _emit(self, level: int, msg: str, fields: dict, exc_info=None) -> None:
        try:
            configure()
            self._log.setLevel(_level(self.service))
            if self._log.isEnabledFor(level):
                self._log.log(level, msg, extra={"fields": fields}, exc_info=exc_info)
        except Exception:
            _count_failure(None, None)

    def debug(self, msg: str, **fields) -> None:
        self._emit(logging.DEBUG, msg, fields)

    def info(self, msg: str, **fields) -> None:
        self._emit(logging.INFO, msg, fields)

    def warning(self, msg: str, **fields) -> None:
        self._emit(logging.WARNING, msg, fields)

    def error(self, msg: str, **fields) -> None:
        self._emit(logging.ERROR, msg, fields)

    def exception(self, msg: str, **fields) -> None:
        self._emit(logging.ERROR, msg, fields, exc_info=sys.exc_info())

    def enabled_debug(self) -> bool:
        return _level(self.service) <= logging.DEBUG


def get_logger(service: str) -> Logger:
    return Logger(service)


def error_fields(exc: BaseException) -> dict:
    """Fields describing a caught exception for a log line: the class always, the message (which can echo
    prompt text, file contents or provider bodies) only with OC_OBS_CAPTURE_CONTENT=1. The full message is still
    on disk in the audit rows and the OpenRouter call record, where `python -m oc.obs tail` finds it."""
    out = {"error": type(exc).__name__}
    if capture_content():
        out["error_message"] = str(exc)[:300]
    return out


def now() -> float:
    return time.time()
