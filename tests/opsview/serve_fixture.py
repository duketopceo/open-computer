"""Start the console on seeded fixture data with a fake passkey session.

Used by the browser tests and the screenshot script. Test-only: the fake Auth
accepts the cookie `oc_sess=test`. Never imported by the product.

    python -I tests/opsview/serve_fixture.py PORT [DATA_DIR] [--empty]
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
    from services.opsview import server
    server.Handler.auth = FakeAuth()
    # The matrix would boot microVMs; fixtures never do.
    server._run_matrix = lambda: "R1: PASS (3 checks)\n1 passed in 0.01s\n"
    print(f"ready http://127.0.0.1:{port}", flush=True)
    server.make_server(port).serve_forever()


if __name__ == "__main__":
    main()
