"""Conformance fixtures: each unit's adapter is bound here via importorskip.

Unbuilt units produce PENDING rows (skipped), never failures. When U1/U2/U3
land, their modules expose adapter(tmp_path) and these rows activate.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def fsproto(tmp_path):
    mod = pytest.importorskip("oc.fsproto", reason="awaiting U1")
    return mod.adapter(tmp_path)


@pytest.fixture
def make_sandbox(tmp_path):
    mod = pytest.importorskip("services.execplane", reason="awaiting U2")
    return mod.adapter(tmp_path).make_sandbox


@pytest.fixture
def execplane(tmp_path):
    mod = pytest.importorskip("services.execplane", reason="awaiting U2")
    return mod.adapter(tmp_path)


@pytest.fixture
def gate(tmp_path):
    mod = pytest.importorskip("services.gate", reason="awaiting U3")
    return mod.adapter(tmp_path)


def pytest_terminal_summary(terminalreporter):
    """Print the determinism matrix: rule -> PASS / FAIL / PENDING."""
    stats = terminalreporter.stats
    rows: dict[str, list[str]] = {}
    for status in ("passed", "failed", "skipped", "error"):
        for rep in stats.get(status, []):
            m = re.search(r"test_r(\d+)_", getattr(rep, "nodeid", ""))
            if m:
                rows.setdefault(f"R{m.group(1)}", []).append(status)
    if not rows:
        return
    terminalreporter.write_sep("=", "determinism matrix")
    for rule in sorted(rows, key=lambda r: int(r[1:])):
        s = rows[rule]
        state = (
            "FAIL"
            if "failed" in s or "error" in s
            else ("PASS" if "passed" in s else "PENDING")
        )
        terminalreporter.write_line(f"{rule}: {state} ({len(s)} checks)")
