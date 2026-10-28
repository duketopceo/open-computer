"""Merge every local record of a run into one time-ordered view, keyed by trace id, run id or goal id.

    python -m oc.obs tail --trace <32-hex>       # or --run <id> / --goal <id>
    python -m oc.obs tail --run <id> --level WARNING --json

Sources (each optional; a missing one is skipped and `--sources` shows which were read):
  log      $OC_LOG_DIR/oc.log*             structured logs (needs OC_LOG_DIR when the run happened)
  audit    $OC_ROOT/orchestrator.ndjson    dispatch/worker audit rows   (or $OC_ORCH_AUDIT, or --audit)
  ledger   $OC_COST_LEDGER                 per-run cost ledger          (or --ledger)
  calls    $OC_OBS_DIR/openrouter-calls.ndjson   one row per OpenRouter request
  events   $OC_EVENTS_DIR/*.ndjson         goal event stream

Rows are matched on `trace_id`, or on a run id (`run_id` / `run` / event `goal`). A trace id is derived from a
run id by hash, so a row that only carries a run id is still found by trace. Everything printed was already
redacted when it was written; the reader redacts again so an old, unredacted file cannot leak through it.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .context import trace_id_for
from .redact import redact

LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
_AUDIT_WARN = {"held_cost_cap", "run_paused", "cloud_call_blocked", "device_pair_refused", "relay_auth_rejected"}
_AUDIT_ERR = {"worker_fail", "run_failed", "cloud_call_failed"}


def _rows(path: Path) -> Iterator[dict]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for ln in text.splitlines():  # a torn last line or a foreign line is skipped, never raised
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict):
            yield r


def _ts(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return 0.0


def sources(args: argparse.Namespace) -> dict[str, list[Path]]:
    env = os.environ.get
    obs = Path(env("OC_OBS_DIR") or Path.home() / ".local/share/oc/obs")
    root = Path(env("OC_ROOT") or Path.home() / ".local/share/oc")
    logd = args.log_dir or env("OC_LOG_DIR")
    ev = env("OC_EVENTS_DIR")
    return {
        "log": sorted(Path(p) for p in glob.glob(str(Path(logd) / "oc.log*"))) if logd else [],
        "audit": [Path(args.audit or env("OC_ORCH_AUDIT") or root / "orchestrator.ndjson")],
        "ledger": [Path(p) for p in (args.ledger or env("OC_COST_LEDGER"),) if p],
        "calls": [obs / "openrouter-calls.ndjson"],
        "events": sorted(Path(p) for p in glob.glob(str(Path(ev) / "*.ndjson"))) if ev else [],
    }


def _normalize(source: str, r: dict) -> dict:
    """-> {ts, source, level, msg, fields, runs, trace}"""
    runs = {str(r[k]) for k in ("run_id", "run") if r.get(k)}
    if source == "events" and r.get("goal"):
        runs.add(str(r["goal"]))
    if source == "events" and isinstance(r.get("data"), dict):
        runs |= {str(r["data"][k]) for k in ("run", "run_id") if r["data"].get(k)}
    if source == "log":
        level = r.get("level", "INFO")
        msg = r.get("msg", "")
        skip = {"ts", "level", "msg"}
    elif source == "calls":
        level = "ERROR" if r.get("status") == "error" else "INFO"
        msg = f"openrouter {r.get('kind', 'call')} {r.get('status', '')}".strip()
        skip = {"ts", "event", "status", "kind", "prompt", "completion"}  # content stays out of the merged view
    elif source == "events":
        level, msg, skip = "INFO", f"event {r.get('type', '')}", {"ts", "type", "data", "id", "v"}
    else:
        ev = str(r.get("event", source))
        level = "ERROR" if ev in _AUDIT_ERR else "WARNING" if ev in _AUDIT_WARN else "INFO"
        msg, skip = f"{source} {ev}", {"ts", "event"}
    fields = {k: v for k, v in r.items() if k not in skip}
    return {"ts": _ts(r.get("ts")), "source": source, "level": level, "msg": msg, "fields": fields,
            "runs": runs, "trace": r.get("trace_id")}


def collect(args: argparse.Namespace) -> list[dict]:
    srcs = sources(args)
    rows = [_normalize(name, r) for name, paths in srcs.items() for p in paths for r in _rows(p)]
    runs: set[str] = set()
    if args.run:
        runs.add(args.run)
    if args.goal:
        runs.add(args.goal)
    if args.trace:
        for n in rows:  # a run belongs to the trace if its id hashes to it, or a row says so
            runs |= {r for r in n["runs"] if trace_id_for(r) == args.trace}
            if n["trace"] == args.trace:
                runs |= n["runs"]
    keep = [n for n in rows if (args.trace and n["trace"] == args.trace) or (n["runs"] & runs)]
    floor = LEVELS.get((args.level or "DEBUG").upper(), 10)
    keep = [n for n in keep if LEVELS.get(n["level"], 20) >= floor]
    keep.sort(key=lambda n: (n["ts"], n["source"]))
    return keep[-args.limit:] if args.limit else keep


def fmt(n: dict) -> str:
    t = datetime.fromtimestamp(n["ts"], timezone.utc).strftime("%H:%M:%S.%f")[:-3] if n["ts"] else "--:--:--"
    kv = " ".join(f"{k}={json.dumps(v, default=str, separators=(',', ':'))}" for k, v in n["fields"].items()
                  if k not in ("trace_id", "run_id", "run", "chat_id", "service"))
    return f"{t} {n['source']:<6} {n['level']:<7} {n['msg']} {kv}".rstrip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m oc.obs", description="Local observability tools.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tail", help="merge logs, audit, ledger, OpenRouter calls and events for one run or trace")
    sel = t.add_mutually_exclusive_group(required=True)
    sel.add_argument("--trace", help="32-hex trace id")
    sel.add_argument("--run", help="run id")
    sel.add_argument("--goal", help="goal id (event stream)")
    t.add_argument("--level", choices=sorted(LEVELS), help="minimum level")
    t.add_argument("--limit", type=int, default=0, help="only the last N rows")
    t.add_argument("--json", action="store_true", help="one JSON object per line")
    for opt in ("--log-dir", "--audit", "--ledger"):
        t.add_argument(opt)
    t.add_argument("--sources", action="store_true", help="print which files were read, then exit")
    a = ap.parse_args(argv)
    if a.sources:
        for name, paths in sources(a).items():
            for p in paths or [None]:
                print(f"{name:<7} {p if p else '(not configured)'}{'' if p is None or p.exists() else '  (missing)'}")
        return 0
    rows = collect(a)
    for n in rows:
        if a.json:
            out = {"ts": n["ts"], "source": n["source"], "level": n["level"], "msg": n["msg"], **n["fields"]}
            print(json.dumps(redact(out), default=str, separators=(",", ":")))
        else:
            print(fmt({**n, "fields": redact(n["fields"])}))
    if not rows:
        print("no matching rows (check --sources, OC_LOG_DIR and the id)", file=sys.stderr)
        return 1
    return 0
