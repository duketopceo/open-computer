"""Start the console on seeded fixture data with a fake passkey session.

Used by the browser tests and the screenshot script. Test-only: the fake Auth
accepts the cookie `oc_sess=test`. Never imported by the product.

    python -I tests/opsview/serve_fixture.py PORT [DATA_DIR] [--empty] [--replay]

--replay serves the recorded workspace goals (services/workspace/fixtures) and adds a test-only
POST /__test/burst?goal=ID&n=N that appends N valid events to a live goal, for the live-stability gate.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.opsview.seed import seed, start_fake_cube  # noqa: E402


class FakeAuth:
    def check_cookie(self, rp, raw):
        return "luke@test" if raw == "test" else None


def install_burst(server):
    from urllib.parse import parse_qs, urlparse

    from services.workspace import routes

    orig = server.Handler.do_POST

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/__test/burst":
            return orig(self)
        q = parse_qs(u.query)
        goal, n = q["goal"][0], int(q.get("n", ["200"])[0])
        em = routes.ws().emitter(goal, {"name": "burst-test", "kind": "agent"})
        em.step("step_burst", "Burst of activity", "running")
        for i in range(n // 2):
            em.tool_call(f"tool_burst{i}", "shell.exec", "ok", step="step_burst", summary=f"command {i}", destination="sbx-burst", duration_ms=5)
            em.note(f"note_burst{i}", "agent", f"Burst message {i} with **bold** text and a citation [1].", kind="message")
        self._json(200, {"ok": True, "events": 1 + (n // 2) * 2})

    server.Handler.do_POST = do_POST


def main():
    port = int(sys.argv[1])
    base = Path(sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else tempfile.mkdtemp(prefix="oc-fixture-"))
    env = seed(base) if "--empty" not in sys.argv else {}
    for k, v in env.items():
        if k != "pending":
            os.environ[k] = v
    if "--empty" in sys.argv:
        os.environ.update({"OC_AUDIT": str(base / "a.ndjson"), "OC_APPROVALS": str(base / "p.ndjson"),
                           "OC_ROUTER_LEDGER": str(base / "l.jsonl"), "OC_RUNS_DIR": str(base / "runs")})
    if "--no-cube" not in sys.argv:
        _, url = start_fake_cube()
        os.environ["CUBE_API_URL"] = url
        os.environ["CUBE_API_KEY"] = "fixture"
    os.environ.pop("OPENROUTER_API_KEY", None)
    if "--replay" in sys.argv:
        os.environ["OC_REPLAY"] = "1"
        os.environ["OC_WORKSPACE_DIR"] = str(base / "workspace")
    from services.opsview import server
    server.Handler.auth = FakeAuth()
    if "--replay" in sys.argv:
        install_burst(server)
    # The matrix would boot microVMs; fixtures never do.
    server._run_matrix = lambda: "R1: PASS (3 checks)\n1 passed in 0.01s\n"
    print(f"ready http://127.0.0.1:{port}", flush=True)
    server.make_server(port).serve_forever()


if __name__ == "__main__":
    main()
