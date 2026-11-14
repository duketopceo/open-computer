"""Bounded reading of archive metadata.

`services/workspace/routes.py` lists a run artifact's zip entries for the console. Nothing in this tree extracts an
entry, so the exposure is the listing itself: an entry count and an accumulated name/metadata size are both
attacker-controlled, and `z.infolist()` builds the whole list before anything can reject it.

This module bounds the listing. It is deliberately not a decompression guard. A decompression bound would protect
a code path that does not exist, and adding an extractor dependency to justify it would widen the supply chain to
guard nothing. If an extractor is ever added, it inherits these constants.

Sizes are checked before an archive is opened where that is possible: the byte size is available from `stat()` and
costs nothing to refuse.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

# A run artifact is a packaged workspace, not a disk image. These mirror the MAX_* convention in
# services/opsview/artifacts.py.
MAX_INPUT = 10 * 1024 * 1024
MAX_ENTRIES = 4096
MAX_LISTING = 1024 * 1024


class ArchiveTooLarge(ValueError):
    """The archive, or its listing, exceeds a bound. The message names which bound and the observed value."""


def check_size(path: Path) -> int:
    """Refuse an archive over `MAX_INPUT` before it is opened. Returns its byte size."""
    n = path.stat().st_size
    if n > MAX_INPUT:
        raise ArchiveTooLarge(f"archive is {n} bytes, over the {MAX_INPUT}-byte limit")
    return n


def entries(path: Path) -> list[dict]:
    """Every entry's `{path, size, dir}`, bounded.

    The entry count is checked before the full list is built, and the accumulated listing is checked as it grows,
    so a crafted archive cannot make this allocate without limit.
    """
    check_size(path)
    out: list[dict] = []
    used = 0
    try:
        with zipfile.ZipFile(path) as z:
            for i in z.infolist():
                if len(out) >= MAX_ENTRIES:
                    raise ArchiveTooLarge(f"archive has more than {MAX_ENTRIES} entries")
                row = {"path": i.filename, "size": i.file_size, "dir": i.is_dir()}
                used += len(i.filename) + len(str(i.file_size))
                if used > MAX_LISTING:
                    raise ArchiveTooLarge(f"entry listing exceeds the {MAX_LISTING}-byte limit")
                out.append(row)
    except zipfile.BadZipFile as e:
        raise ArchiveTooLarge("this archive can't be listed") from e
    return out


__all__ = ["ArchiveTooLarge", "MAX_ENTRIES", "MAX_INPUT", "MAX_LISTING", "check_size", "entries"]
