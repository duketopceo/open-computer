"""Goal event stream: schema, validator, logs, emitter. See docs/design/event-stream.md."""

from .emitter import ApprovalStateError, Emitter, payload_hash
from .log import TERMINAL, FileLog, MemoryLog
from .summary import entities, fold, group
from .validate import errors, schema, validate

__all__ = [
    "ApprovalStateError",
    "Emitter",
    "entities",
    "FileLog",
    "MemoryLog",
    "TERMINAL",
    "errors",
    "fold",
    "group",
    "payload_hash",
    "schema",
    "validate",
]
