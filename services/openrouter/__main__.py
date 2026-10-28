"""CLI:  python -m services.openrouter report [--by chat|run|subtask|model|provider|kind|day|status] [--run R] [--chat C] [--json]
         python -m services.openrouter reconcile [--min-age SECONDS]   (needs OPENROUTER_API_KEY)"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .generation import reconcile
from .store import GROUPS, CallStore, summarize


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m services.openrouter")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report")
    r.add_argument("--by", default="chat", choices=GROUPS)
    r.add_argument("--run")
    r.add_argument("--chat")
    r.add_argument("--json", action="store_true")
    rc = sub.add_parser("reconcile")
    rc.add_argument("--min-age", type=float, default=30.0)
    a = ap.parse_args(argv)
    store = CallStore()
    if a.cmd == "report":
        rows = summarize(store.calls(), a.by, run=a.run, chat=a.chat)
        if a.json:
            print(json.dumps(rows, indent=2))
            return 0
        print(f"{a.by:<28} {'calls':>5} {'err':>4} {'cost $':>12} {'tok in':>9} {'tok out':>9} {'p50 ms':>7} {'p95 ms':>7} {'no-cost':>7} {'mism':>4}")
        for g in rows:
            print(f"{g['key'][:28]:<28} {g['calls']:>5} {g['errors']:>4} {g['cost']:>12.6f} {g['tokens_in']:>9} "
                  f"{g['tokens_out']:>9} {g['latency_ms_p50'] or 0:>7.0f} {g['latency_ms_p95'] or 0:>7.0f} "
                  f"{g['no_cost_reported']:>7} {g['mismatches']:>4}")
        print(f"\nstore: {store.path}")
        return 0
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 2
    print(json.dumps(reconcile(store, key, min_age=a.min_age)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
