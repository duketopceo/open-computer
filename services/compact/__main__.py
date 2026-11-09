"""`python -m services.compact restore <thread> <range>`: print a tombstoned range of a compacted thread."""

from __future__ import annotations

import sys

from services.compact import CompactConfig, Compactor


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] != "restore":
        print("usage: python -m services.compact restore <thread> <range, e.g. s4-s9>", file=sys.stderr)
        return 2
    text = Compactor(CompactConfig()).restore(argv[1], argv[2])
    if text is None:
        print(f"no such range {argv[2]!r} for {argv[1]}", file=sys.stderr)
        return 1
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
