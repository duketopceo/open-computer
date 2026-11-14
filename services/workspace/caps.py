"""Bounded reading of archive metadata.

`services/workspace/routes.py` lists a run artifact's zip entries for the console. Nothing in this tree extracts an
entry, so the exposure is the listing itself: the entry count and the accumulated name/metadata size are both
attacker-controlled.

The bound has to be applied before `zipfile.ZipFile` runs, not while walking its result. `ZipFile.__init__` reads
the whole central directory into `self.filelist` at open time, so `infolist()` returns a copy of a list that was
already materialized: a 10 MB archive holding 120,000 empty entries allocates ~67 MB inside `ZipFile()` before any
per-entry check can fire. The end-of-central-directory record states the entry count in a fixed-width field, so it
can be read from the last few bytes without parsing anything.

This module is deliberately not a decompression guard. A decompression bound would protect a code path that does
not exist, and adding an extractor dependency to justify it would widen the supply chain to guard nothing. If an
extractor is ever added, it inherits these constants.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

# A run artifact is a packaged workspace, not a disk image. These mirror the MAX_* convention in
# services/opsview/artifacts.py.
MAX_INPUT = 10 * 1024 * 1024
MAX_ENTRIES = 4096
MAX_LISTING = 1024 * 1024

# The end-of-central-directory record is 22 bytes plus an optional comment of at most 65,535. The entry count sits
# 12 bytes into the record as a 2-byte field.
_EOCD = b"PK\x05\x06"
_EOCD_MIN = 22
_COMMENT_MAX = 65_535


class ArchiveTooLarge(ValueError):
    """The archive, or its listing, exceeds a bound. The message names which bound and the observed value."""


def check_size(path: Path) -> int:
    """Refuse an archive over `MAX_INPUT` before it is opened. Returns its byte size."""
    n = path.stat().st_size
    if n > MAX_INPUT:
        raise ArchiveTooLarge(f"archive is {n} bytes, over the {MAX_INPUT}-byte limit")
    return n


def count_entries(path: Path) -> int | None:
    """The entry count from the end-of-central-directory record, or None when there is no readable record.

    None means "not a zip this module can bound from the header alone"; the caller then lets `zipfile` decide, which
    reports a `BadZipFile` for a non-archive. ZIP64 archives report 0xFFFF entries here and are refused: a workspace
    artifact with 65,535 entries is already past every bound below.
    """
    size = path.stat().st_size
    tail_len = min(size, _EOCD_MIN + _COMMENT_MAX)
    with path.open("rb") as f:
        f.seek(size - tail_len)
        tail = f.read(tail_len)
    at = tail.rfind(_EOCD)
    if at < 0 or at + _EOCD_MIN > len(tail):
        return None
    (count,) = struct.unpack("<H", tail[at + 10 : at + 12])
    if count == 0xFFFF:  # ZIP64 sentinel: the real count lives in a ZIP64 record this module does not follow
        raise ArchiveTooLarge(f"archive declares more than the {MAX_ENTRIES}-entry limit")
    return count


def entries(path: Path) -> list[dict]:
    """Every entry's `{path, size, dir}`, bounded before and during the walk."""
    check_size(path)
    declared = count_entries(path)
    if declared is not None and declared > MAX_ENTRIES:
        raise ArchiveTooLarge(f"archive declares {declared} entries, over the {MAX_ENTRIES}-entry limit")
    out: list[dict] = []
    used = 0
    try:
        with zipfile.ZipFile(path) as z:
            for i in z.infolist():
                # The header count can disagree with the directory a crafted archive actually contains.
                if len(out) >= MAX_ENTRIES:
                    raise ArchiveTooLarge(f"archive has more than {MAX_ENTRIES} entries")
                row = {"path": i.filename, "size": i.file_size, "dir": i.is_dir()}
                used += len(i.filename) + len(str(i.file_size))
                if used > MAX_LISTING:
                    raise ArchiveTooLarge(f"entry listing exceeds the {MAX_LISTING}-byte limit")
                out.append(row)
    except zipfile.BadZipFile as e:
        raise ArchiveTooLarge("it is not a readable zip") from e
    return out


__all__ = ["ArchiveTooLarge", "MAX_ENTRIES", "MAX_INPUT", "MAX_LISTING", "check_size", "count_entries", "entries"]
