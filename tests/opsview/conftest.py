"""Shared fixtures. Never boots microVMs, never calls a model, never leaves
127.0.0.1: the control plane is an in-process fake and the matrix runner and
router are replaced with recorders."""

from __future__ import annotations

import http.client
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from seed import seed, start_fake_cube  # noqa: E402

from services.opsview import server  # noqa: E402

COOKIE = "oc_sess=test"


class FakeAuth:
    def check_cookie(self, rp, raw):
        return "luke@test" if raw == "test" else None

    def local_session(self, rp):
        return "test"

    def access_session(self, rp, email):
        return "test"


class Console:
    def __init__(self, port: int, base: Path, env: dict):
        self.port, self.base, self.env = port, base, env

    def request(
        self,
        method: str,
        path: str,
        *,
        body: str | bytes | None = None,
        headers: dict | None = None,
        cookie: bool = True,
    ):
        h = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        if cookie:
            h["Cookie"] = COOKIE
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data.decode(errors="replace")

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def csrf(self) -> str:
        import re

        _, _, html = self.get("/runs/new")
        return re.search(r'name="csrf-token" content="([0-9a-f]+)"', html).group(1)

    def post(self, path, fields: dict, *, csrf: bool | str = True, headers: dict | None = None, cookie=True):
        from urllib.parse import urlencode

        data = dict(fields)
        if csrf is True:
            data["csrf"] = self.csrf()
        elif csrf:
            data["csrf"] = csrf
        return self.request(
            "POST",
            path,
            body=urlencode(data),
            headers={"Content-Type": "application/x-www-form-urlencoded", **(headers or {})},
            cookie=cookie,
        )

    def snapshot(self) -> dict:
        return {
            str(p.relative_to(self.base)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(self.base.rglob("*"))
            if p.is_file()
        }


@pytest.fixture
def calls(monkeypatch):
    """Recorders standing in for everything that boots, spends or leaves the box."""
    rec = {"matrix": 0, "route": 0}

    def fake_matrix():
        rec["matrix"] += 1
        return "R1: PASS (3 checks)\nR2: PENDING (2 checks)\n1 passed in 0.01s\n"

    def fake_route(q):
        rec["route"] += 1
        return {"route": "local", "tier": "cheap", "engine": "table", "confidence": 0.9, "cost": 0.0, "ms": 1}

    monkeypatch.setattr(server, "_run_matrix", fake_matrix)
    monkeypatch.setattr(server, "_route", fake_route)
    return rec


@pytest.fixture
def console(tmp_path, monkeypatch, calls):
    env = seed(tmp_path / "data")
    for k, v in env.items():
        if k != "pending":
            monkeypatch.setenv(k, v)
    cube, url = start_fake_cube()
    monkeypatch.setenv("CUBE_API_URL", url)
    monkeypatch.setenv("CUBE_API_KEY", "fixture")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(server.Handler, "auth", FakeAuth())
    srv = server.make_server(0)
    threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.01), daemon=True).start()
    yield Console(srv.server_address[1], tmp_path / "data", env)
    srv.shutdown()
    cube.shutdown()
