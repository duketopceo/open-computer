"""Conformance fixtures: each unit's adapter is bound here via importorskip.

Unbuilt units produce PENDING rows (skipped), never failures. When U1/U2/U3
land, their modules expose adapter(tmp_path) and these rows activate.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # harness, fakes, checks
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root: oc/, services/


@pytest.fixture
def fsproto(tmp_path):
    mod = pytest.importorskip("oc.fsproto", reason="awaiting U1")
    return mod.adapter(tmp_path)


@pytest.fixture
def make_sandbox(tmp_path):
    mod = pytest.importorskip("services.execplane", reason="awaiting U2")
    a = mod.adapter(tmp_path)
    if not a.sandboxes_available():
        pytest.skip("CubeSandbox unreachable — sandbox rows stay PENDING")
    return a.make_sandbox


@pytest.fixture
def execplane(tmp_path):
    mod = pytest.importorskip("services.execplane", reason="awaiting U2")
    return mod.adapter(tmp_path)


@pytest.fixture
def gate(tmp_path):
    mod = pytest.importorskip("services.gate", reason="awaiting U3")
    return mod.adapter(tmp_path)


@pytest.fixture
def ops(tmp_path):
    mod = pytest.importorskip("services.opsview.server", reason="awaiting F-AUTH")
    auth_mod = pytest.importorskip("services.opsview.auth", reason="awaiting F-AUTH")
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    auth = auth_mod.Auth(
        store=auth_mod.CredentialStore(tmp_path / "creds.json"),
        key_path=tmp_path / "session-key",
    )
    mod.Handler.auth = auth
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None

    opener = urllib.request.build_opener(_NoRedirect)

    def get(path, method="GET", data=None, headers=None):
        req = urllib.request.Request(
            base + path, method=method, data=data, headers=headers or {}
        )
        return opener.open(req, timeout=5)

    yield base, auth, get
    srv.shutdown()


def pytest_terminal_summary(terminalreporter):
    """Print the determinism matrix: rule -> PASS / FAIL / PENDING."""
    stats = terminalreporter.stats
    rows: dict[str, list[str]] = {}
    for status in ("passed", "failed", "skipped", "error"):
        for rep in stats.get(status, []):
            m = re.search(r"test_(r\d+|router|auth)_", getattr(rep, "nodeid", ""))
            if m:
                rows.setdefault(m.group(1).upper(), []).append(status)
    if not rows:
        return
    terminalreporter.write_sep("=", "determinism matrix")
    for rule in sorted(rows, key=lambda r: (0, int(r[1:])) if r[1:].isdigit() else (1, r)):
        s = rows[rule]
        state = (
            "FAIL"
            if "failed" in s or "error" in s
            else ("PASS" if "passed" in s else "PENDING")
        )
        terminalreporter.write_line(f"{rule}: {state} ({len(s)} checks)")
