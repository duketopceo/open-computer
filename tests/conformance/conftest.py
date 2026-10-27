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


_BACKENDS = ("cube", "space")


@pytest.fixture(params=_BACKENDS)
def backend(request):
    return request.param


@pytest.fixture
def make_sandbox(tmp_path, backend):
    mod = pytest.importorskip("services.execplane", reason="awaiting U2")
    a = mod.adapter(tmp_path, backend=backend)
    # Honest skip only: an adapter that cannot build or probe fails loudly.
    if not a.sandboxes_available():
        pytest.skip(f"{backend} backend unreachable — sandbox rows stay PENDING")
    # S5: positive-control hosts must be human-approved before create.
    prop = a.policy.propose({"add_egress": ["example.com", "example.org"]}, origin="human")
    a.policy.apply(prop, approved_by="human")
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
        req = urllib.request.Request(base + path, method=method, data=data, headers=headers or {})
        return opener.open(req, timeout=5)

    yield base, auth, get
    srv.shutdown()


def pytest_terminal_summary(terminalreporter):
    """Print the determinism matrix: rule -> PASS / FAIL / PENDING."""
    stats = terminalreporter.stats
    rows: dict[str, list[str]] = {}
    for status in ("passed", "failed", "skipped", "error", "deselected"):
        for rep in stats.get(status, []):
            nodeid = getattr(rep, "nodeid", "")
            m = re.search(r"test_(r\d+|router|auth|place|orch|work)_", nodeid)
            if m:
                param = re.search(r"\[(cube|space)\]", nodeid)
                key = f"{m.group(1).upper()}[{param.group(1)}]" if param else m.group(1).upper()
                rows.setdefault(key, []).append(status)
    if not rows:
        return
    terminalreporter.write_sep("=", "determinism matrix")

    def _sort(key):
        rule, _, param = key.partition("[")
        n = rule[1:]
        return (0, int(n), param) if n.isdigit() else (1, rule, param)

    for key in sorted(rows, key=_sort):
        s = rows[key]
        state = (
            "FAIL"
            if "failed" in s or "error" in s
            else ("PASS" if "passed" in s else "LIVE-ONLY" if set(s) == {"deselected"} else "PENDING")
        )
        terminalreporter.write_line(f"{key}: {state} ({len(s)} checks)")
