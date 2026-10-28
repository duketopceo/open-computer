"""`python -m oc.obs tail`: one time-ordered view of a run across logs, audit, ledger, call record and events."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from oc.obs import context
from oc.obs.tail import main

ROOT = Path(__file__).resolve().parents[2]
RUN = "run-aaa"
OTHER = "run-bbb"
TRACE = context.trace_id_for(RUN)


def write(path: Path, rows: list, torn: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows) + torn)
    return path


@pytest.fixture
def world(tmp_path, monkeypatch):
    logd, root, events = tmp_path / "logs", tmp_path / "root", tmp_path / "events"
    iso = lambda s: f"2026-10-28T12:00:{s:06.3f}+00:00"  # noqa: E731
    write(logd / "oc.log", [
        {"ts": iso(1), "level": "INFO", "service": "orchestrator", "msg": "run submitted", "run_id": RUN, "trace_id": TRACE},
        {"ts": iso(4), "level": "WARNING", "service": "cost", "msg": "run paused: cap", "run_id": RUN, "trace_id": TRACE},
        {"ts": iso(2), "level": "INFO", "service": "orchestrator", "msg": "run submitted", "run_id": OTHER,
         "trace_id": context.trace_id_for(OTHER)},
        {"ts": iso(5), "level": "DEBUG", "service": "router", "msg": "route input", "run_id": RUN, "trace_id": TRACE},
    ], torn='{"ts": "2026-10-28T12:00:09')
    ts0 = 1793188800.0  # 2026-10-28T12:00:00Z
    write(root / "orchestrator.ndjson", [
        {"event": "dispatch", "run": RUN, "task": "t1", "subtask": "a", "ts": ts0 + 3},  # run id only, no trace id
        {"event": "worker_fail", "run": RUN, "task": "t1", "error": "boom", "ts": ts0 + 6},
        {"event": "dispatch", "run": OTHER, "task": "t9", "ts": ts0 + 3},
    ])
    write(tmp_path / "cost.ndjson", [{"event": "cost", "run": RUN, "subtask": "a", "cost": 0.004, "ts": ts0 + 3.5}])
    write(tmp_path / "obs" / "openrouter-calls.ndjson", [
        {"event": "call", "kind": "chat", "status": "ok", "run_id": RUN, "trace_id": TRACE, "cost": 0.004, "ts": ts0 + 3.2,
         "prompt": "PRIVATE PROMPT", "completion": "PRIVATE COMPLETION"},
        {"event": "call", "kind": "chat", "status": "error", "run_id": RUN, "trace_id": TRACE, "ts": ts0 + 7},
    ])
    write(events / "goal-1.ndjson", [{"goal": RUN, "seq": 1, "type": "run", "ts": ts0 + 8, "data": {"status": "done"}}])
    for k, v in (("OC_LOG_DIR", logd), ("OC_ROOT", root), ("OC_COST_LEDGER", tmp_path / "cost.ndjson"),
                 ("OC_EVENTS_DIR", events)):
        monkeypatch.setenv(k, str(v))
    return tmp_path


def run(capsys, *argv):
    rc = main(["tail", *argv])
    out = capsys.readouterr()
    return rc, out.out.splitlines(), out.err


def test_run_selects_every_source_in_time_order_and_skips_other_runs(world, capsys):
    rc, lines, _ = run(capsys, "--run", RUN)
    assert rc == 0
    sources = [ln.split()[1] for ln in lines]
    assert sources == ["log", "audit", "calls", "ledger", "log", "log", "audit", "calls", "events"]
    assert not any(OTHER in ln or "t9" in ln for ln in lines)
    assert [ln.split()[0] for ln in lines] == sorted(ln.split()[0] for ln in lines)


def test_trace_finds_rows_that_only_carry_the_run_id(world, capsys):
    _, by_trace, _ = run(capsys, "--trace", TRACE)
    _, by_run, _ = run(capsys, "--run", RUN)
    assert by_trace == by_run and any(" audit " in ln for ln in by_trace)


def test_level_filter_keeps_warnings_and_errors_from_every_source(world, capsys):
    _, lines, _ = run(capsys, "--run", RUN, "--level", "WARNING")
    assert [(ln.split()[1], ln.split()[2]) for ln in lines] == [
        ("log", "WARNING"), ("audit", "ERROR"), ("calls", "ERROR")]


def test_prompt_and_completion_never_appear_in_the_merged_view(world, capsys):
    _, lines, _ = run(capsys, "--run", RUN)
    _, jlines, _ = run(capsys, "--run", RUN, "--json")
    assert "PRIVATE" not in "\n".join(lines + jlines)
    assert all(json.loads(ln) for ln in jlines)


def test_output_is_redacted_even_if_the_file_was_not(world, capsys):
    secret = "sk-or-v1-0123456789abcdef0123456789abcdef"
    write(world / "root" / "orchestrator.ndjson", [{"event": "worker_fail", "run": RUN, "error": f"bad {secret}", "ts": 1.0}])
    _, lines, _ = run(capsys, "--run", RUN)
    assert secret not in "\n".join(lines)


def test_limit_and_unknown_id_and_torn_lines(world, capsys):
    _, lines, _ = run(capsys, "--run", RUN, "--limit", "2")
    assert len(lines) == 2
    rc, lines, err = run(capsys, "--run", "nope")
    assert rc == 1 and lines == [] and "no matching rows" in err


def test_sources_lists_what_it_reads_and_what_is_missing(world, capsys, monkeypatch):
    monkeypatch.delenv("OC_EVENTS_DIR")
    rc, lines, _ = run(capsys, "--run", RUN, "--sources")
    assert rc == 0
    text = "\n".join(lines)
    assert "oc.log" in text and "orchestrator.ndjson" in text and "openrouter-calls.ndjson" in text
    assert "events" in text and "(not configured)" in text


def test_module_entry_point_runs(world):
    env = {"PATH": "/usr/bin:/bin", "OC_LOG_DIR": str(world / "logs"), "OC_ROOT": str(world / "root"),
           "OC_OBS_DIR": str(world / "obs"), "HOME": str(world)}
    p = subprocess.run([sys.executable, "-m", "oc.obs", "tail", "--run", RUN, "--level", "ERROR"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 0 and "worker_fail" in p.stdout
