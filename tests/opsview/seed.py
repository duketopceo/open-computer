"""Deterministic fixture data for console tests and screenshots. No network,
no microVMs, no models: everything is written to a temp dir and read back."""

from __future__ import annotations

import http.server
import json
import threading
import time
from pathlib import Path

from services.gate.audit import AuditLog
from services.gate.gate import Approval, Gate
from services.gate.queue import ApprovalQueue
from services.opsview import runs

NOW = 1_791_700_000.0  # fixed epoch so relative times are stable when passed explicitly


def seed(base: Path, now: float | None = None) -> dict:
    """Populate audit, ledger, approvals and runs under `base`. Returns env vars."""
    now = time.time() if now is None else now
    base.mkdir(parents=True, exist_ok=True)
    audit = base / "audit.ndjson"
    gate = Gate(audit)
    q = ApprovalQueue(base / "approvals.ndjson", gate)
    q.submit(who="docs-agent", session="sess-4f21", ts=now - 5400, est_cost=0.0021,
             what="Summarise the v0.3 changelog with a frontier model",
             payload={"model": "anthropic/claude-sonnet", "egress": "openrouter.ai",
                      "prompt": "Summarise the changelog for release v0.3 in five bullets."})
    q.submit(who="deploy-agent", session="sess-9ab0", ts=now - 1500, est_cost=0.0187,
             what="Draft customer reply that quotes an invoice",
             payload={"model": "openai/gpt-5.2", "egress": "api.openai.com",
                      "prompt": "Reply to jane@example.com about SSN 123-45-6789 on invoice 4471."})
    q.submit(who="research-agent", session="sess-77c3", ts=now - 240, est_cost=0.0009,
             what="Classify 40 support tickets",
             payload={"model": "perplexity/pplx-decider", "egress": "openrouter.ai",
                      "prompt": "Classify tickets: refund, outage, how-to."})
    done = q.submit(who="docs-agent", session="sess-4f21", ts=now - 86400 * 2, est_cost=0.0004,
                    what="Translate README intro", payload={"model": "google/gemini-flash", "egress": "openrouter.ai", "prompt": "Translate."})
    q.decide(done, approve=True, by="luke", note="")
    gate.request_cloud_call(q.get(done)["payload"], Approval(done, q.get(done)["payload_hash"]),
                            sender=lambda p: len(str(p)))  # a real, consumed send: the audit shows both rows
    qf = base / "approvals.ndjson"
    evs = [json.loads(line) for line in qf.read_text().splitlines()]
    for ev in evs:
        if ev["event"] == "decision":
            ev["ts"] = now - 86400 * 2 + 120
    qf.write_text("".join(json.dumps(ev) + "\n" for ev in evs))
    # backdate the audit rows written by decide() so the table reads naturally
    rows = AuditLog(audit).rows()
    audit.write_text("".join(json.dumps({**r, "ts": now - 86400 * 2 + i * 3}) + "\n" for i, r in enumerate(rows)))
    gate.audit.append({"event": "cloud_call_blocked", "reason": "unapproved", "model": "openai/gpt-5.2", "payload_bytes": 412})
    ledger = base / "router-ledger.jsonl"
    ledger.write_text("".join(
        json.dumps({"ts": now - 3600 * h, "event": "decision", "engine": eng, "route": route, "cost": cost}) + "\n"
        for h, eng, route, cost in [(5, "table", "local", 0.0), (3, "decider", "frontier", 0.000004), (1, "decider", "cheap", 0.000004)]))
    out = ("tests/conformance/rules/test_r1.py ..                                            [ 20%]\n" * 3
           + "\n".join(f"R{i}: PASS ({i + 2} checks)" for i in (1, 2, 3, 4, 5, 6, 7)) + "\n"
           + "AUTH: PENDING (2 checks)\n7 passed, 1 skipped in 6.91s\n")
    r = runs.record(out, by="luke", duration_s=6.9, base=base / "runs")
    big = "\n".join(f"{i:05d} step collected assertion ok {'x' * (i % 40)}" for i in range(12000)) + "\nR1: PASS (3 checks)\n"
    runs.record(big, by="luke", duration_s=211, base=base / "runs")
    return {
        "OC_AUDIT": str(audit), "OC_APPROVALS": str(base / "approvals.ndjson"),
        "OC_ROUTER_LEDGER": str(ledger), "OC_RUNS_DIR": str(base / "runs"),
        "pending": 3,
    }


class FakeCube(http.server.BaseHTTPRequestHandler):
    """Stands in for the CubeSandbox control plane. Read-only inventory."""

    def do_GET(self):
        if self.path == "/health":
            body = b'{"ok":true}'
        elif self.path == "/sandboxes":
            body = json.dumps([
                {"sandboxID": "sbx-7f3a91c2", "templateID": "code-interpreter", "status": "running"},
                {"sandboxID": "sbx-2d10be44", "templateID": "code-interpreter", "status": "running"},
            ]).encode()
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def start_fake_cube(port: int = 0) -> tuple[http.server.ThreadingHTTPServer, str]:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), FakeCube)
    threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.01), daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
