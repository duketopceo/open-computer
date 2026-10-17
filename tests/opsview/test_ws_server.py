"""Workspace routes: SSE with resume, replay, approval safety, artifacts. Nothing boots, spends or leaves 127.0.0.1."""

from __future__ import annotations

import http.client
import json
import re
import threading
import time
from pathlib import Path

import pytest

from oc.events import MemoryLog, entities
from services.opsview import server
from services.workspace import routes

FIX = Path(__file__).resolve().parents[2] / "services/workspace/fixtures"


class FakeAuth:
    def check_cookie(self, rp, raw):
        return "luke@test" if raw == "test" else None

    def local_session(self, rp):
        return "test"

    def access_session(self, rp, email):
        return "test"


class Client:
    def __init__(self, port):
        self.port = port
        self._csrf = None

    def request(self, method, path, body=None, headers=None, cookie=True, timeout=10):
        h = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        if cookie:
            h["Cookie"] = "oc_sess=test"
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data.decode(errors="replace")

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def json(self, path):
        s, _, b = self.get(path)
        assert s == 200, (path, s, b[:200])
        return json.loads(b)

    def csrf(self):
        if not self._csrf:
            _, _, html = self.get("/workspace")
            self._csrf = re.search(r'name="csrf-token" content="([0-9a-f]+)"', html).group(1)
        return self._csrf

    def post(self, path, obj=None, csrf=True, headers=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        if csrf and "X-CSRF-Token" not in h:
            h["X-CSRF-Token"] = self.csrf()
        s, hd, b = self.request("POST", path, body=json.dumps(obj or {}), headers=h)
        try:
            return s, json.loads(b)
        except ValueError:
            return s, {"raw": b}

    def sse(self, path, headers=None, max_events=10_000, timeout=6):
        """Read an event stream to its end (or max_events). Returns [(id, event, data-dict)] plus raw comments."""
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        c.request("GET", path, headers={"Host": f"127.0.0.1:{self.port}", "Cookie": "oc_sess=test", **(headers or {})})
        r = c.getresponse()
        assert r.status == 200 and r.getheader("Content-Type").startswith("text/event-stream")
        out, cur, comments = [], {}, []
        deadline = time.time() + timeout
        try:
            for raw in r:
                if time.time() > deadline:
                    break
                ln = raw.decode().rstrip("\n")
                if ln == "":
                    if "data" in cur or "event" in cur:
                        out.append((cur.get("id"), cur.get("event", "message"), json.loads(cur.get("data", "{}"))))
                        if len(out) >= max_events or cur.get("event") == "end":
                            break
                    cur = {}
                elif ln.startswith(":"):
                    comments.append(ln)
                else:
                    k, _, v = ln.partition(": ")
                    cur[k] = v
        except (TimeoutError, OSError):
            pass
        c.close()
        return out, comments


@pytest.fixture
def wsx(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_REPLAY", "1")
    monkeypatch.setenv("OC_WORKSPACE_DIR", str(tmp_path / "ws"))
    monkeypatch.setenv("OC_AUDIT", str(tmp_path / "audit.ndjson"))
    monkeypatch.setenv("OC_APPROVALS", str(tmp_path / "approvals.ndjson"))
    monkeypatch.setattr(server.Handler, "auth", FakeAuth())
    routes.reset()
    srv = server.make_server(0)
    threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.01), daemon=True).start()
    c = Client(srv.server_address[1])
    c.dir = tmp_path
    yield c
    routes.reset()
    srv.shutdown()


def start(c, goal, speed=0, hold=True, restart=False):
    s, b = c.post(f"/goals/{goal}/replay", {"action": "restart" if restart else "start", "speed": speed, "hold": hold})
    assert s == 200, b


def wait_for(fn, what, t=5):
    end = time.time() + t
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.02)
    raise AssertionError("timed out waiting for " + what)


def last_status(c, goal):
    evs = routes.ws().log.read(goal)
    return evs[-1] if evs else None


# ---- SSE + resume ---------------------------------------------------------------------------------------------


def sse_all(c, goal):
    start(c, goal, hold=False)
    wait_for(lambda: routes.ws().log.finished(goal), "recording")
    return c.sse(f"/goals/{goal}/events")[0][:-1]


def test_sse_streams_every_event_in_order_then_ends(wsx):
    start(wsx, "g_research01", hold=False)
    evs, _ = wsx.sse("/goals/g_research01/events")
    assert evs[-1][1] == "end"
    body = evs[:-1]
    assert [int(i) for i, _, _ in body] == list(range(1, len(body) + 1))
    assert all(name == "message" and i == str(d["seq"]) for i, name, d in body), "data events are unnamed"
    assert any(d["type"] == "error" for _, _, d in sse_all(wsx, "g_coding01")), "an error-typed event must not be an SSE event named error"
    assert len(body) == len([ln for ln in (FIX / "g_research01.ndjson").read_text().splitlines()])


def test_last_event_id_resumes_without_loss_or_duplication(wsx):
    start(wsx, "g_research01", hold=False)
    full, _ = wsx.sse("/goals/g_research01/events")
    first, _ = wsx.sse("/goals/g_research01/events", max_events=20)
    last = first[-1][0]
    rest, _ = wsx.sse("/goals/g_research01/events", headers={"Last-Event-ID": last})
    assert [i for i, e, _ in first] + [i for i, e, _ in rest if e != "end"] == [i for i, e, _ in full if e != "end"]
    q, _ = wsx.sse(f"/goals/g_research01/events?last_event_id={last}")
    assert [i for i, _, _ in q] == [i for i, _, _ in rest]


def test_a_client_claiming_a_future_seq_is_told_to_start_over(wsx):
    start(wsx, "g_research01", hold=False)
    wait_for(lambda: routes.ws().log.finished("g_research01"), "recording to finish")
    evs, _ = wsx.sse("/goals/g_research01/events", headers={"Last-Event-ID": "9999"})
    assert evs[0][1] == "reset" and evs[1][0] == "1"


def test_heartbeat_while_a_run_waits_on_a_human(wsx):
    start(wsx, "g_coding01")  # held at the first approval
    wait_for(lambda: routes.ws().sessions["g_coding01"].waiting_for(), "hold")
    evs, comments = wsx.sse("/goals/g_coding01/events?hb=0.1", timeout=1.0)
    assert evs[-1][2]["data"]["status"] == "pending" and any(c.startswith(": hb") for c in comments)


def test_unauthenticated_stream_is_refused(wsx):
    # loopback is a trusted surface (auto-mints a local session) — an
    # unauthenticated request must come from a non-loopback host
    s, h, _ = wsx.get("/goals/g_research01/events", cookie=False,
                      headers={"Host": "oc.shippedit.dev"})
    assert s == 302 and h["Location"] == "/login"


def test_unknown_goal_and_bad_ids_are_404(wsx):
    assert wsx.get("/goals/g_nope/events")[0] == 404
    assert wsx.get("/goals/g_nope")[0] == 404
    assert wsx.get("/goals/../etc/passwd/events")[0] in (404, 302)


# ---- no GET has a side effect ------------------------------------------------------------------------------------


def snapshot(base: Path):
    return {str(p.relative_to(base)): (p.stat().st_size, p.stat().st_mtime_ns) for p in sorted(base.rglob("*")) if p.is_file()}


def test_every_workspace_get_is_side_effect_free(wsx):
    start(wsx, "g_coding01")
    wait_for(lambda: routes.ws().sessions["g_coding01"].waiting_for(), "hold")
    before, n_events = snapshot(wsx.dir), routes.ws().log.last_seq("g_coding01")
    paths = ["/workspace", "/workspace/g_coding01", "/goals.json", "/goals/g_coding01/state", "/goals/g_coding01/approvals",
             "/goals/g_coding01/artifacts/art_patch", "/goals/g_coding01/artifacts/art_zip/listing",
             "/goals/g_coding01/artifacts/art_patch?download=1", "/workspace/g_cap01", "/static/ws/app.js",
             "/workspace/g_coding01?approve=1&decide=once", "/goals/g_coding01/events?stop=1&resume=1"]
    for p in paths:
        wsx.sse(p, timeout=0.5) if p.endswith("/events") or "/events?" in p else wsx.get(p)
    assert snapshot(wsx.dir) == before
    assert routes.ws().log.last_seq("g_coding01") == n_events
    assert routes.ws().sessions.get("g_cap01") is None, "viewing a goal must not start it: replay starts by POST"


def test_get_handlers_never_name_a_mutating_call():
    import ast
    import inspect
    import textwrap
    forbidden = {"decide", "submit", "append", "emit", "start_replay", "release", "approval_resolve", "approval_request", "run"}
    for _, name in routes.GET_ROUTES:
        src = textwrap.dedent(inspect.getsource(getattr(routes, name)))
        names = {n.id for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Attribute)}
        assert not names & forbidden, (name, names & forbidden)


def test_every_workspace_post_needs_csrf_session_and_same_origin(wsx):
    posts = ["/workspace/new", "/goals/g_coding01/messages", "/goals/g_coding01/stop", "/goals/g_cap01/resume",
             "/goals/g_coding01/approvals/ap_clone01/decide", "/goals/g_research01/artifacts/art_pdf/export",
             "/goals/g_coding01/steps/step_fix/stop", "/goals/g_coding01/replay"]
    for p in posts:
        assert wsx.post(p, {"choice": "once"}, csrf=False)[0] == 403, p
        assert wsx.post(p, {}, headers={"X-CSRF-Token": "0" * 64})[0] == 403, p
        assert wsx.post(p, {}, headers={"Origin": "http://evil.example"})[0] == 403, p
        # loopback auto-mints a local session, so the auth boundary there is CSRF
        s, _, _ = wsx.request("POST", p, body="{}", headers={"Content-Type": "application/json"}, cookie=False)
        assert s == 403, p
        # a truly unauthenticated request (non-loopback host) is refused outright
        s, _, _ = wsx.request("POST", p, body="{}", headers={"Content-Type": "application/json", "Host": "oc.shippedit.dev"}, cookie=False)
        assert s == 401, p
    assert routes.ws().log.goals() == []


# ---- replay + approvals ---------------------------------------------------------------------------------------------


def test_replay_without_hold_reproduces_the_recording_exactly_twice(wsx):
    rec = [json.loads(ln) for ln in (FIX / "g_coding01.ndjson").read_text().splitlines()]
    runs = []
    for i in range(2):
        start(wsx, "g_coding01", hold=False, restart=i == 1)
        wait_for(lambda: routes.ws().log.finished("g_coding01"), "recording")
        runs.append(routes.ws().log.read("g_coding01"))
    assert runs[0] == runs[1] == rec


def test_hold_pauses_at_the_approval_until_a_human_decides(wsx):
    start(wsx, "g_coding01")
    sess = routes.ws().sessions["g_coding01"]
    wait_for(lambda: sess.waiting_for() == "approval:ap_clone01", "first hold")
    n = routes.ws().log.last_seq("g_coding01")
    time.sleep(0.2)
    assert routes.ws().log.last_seq("g_coding01") == n, "replay ran past an unanswered approval"
    ap = entities(routes.ws().log.read("g_coding01"), "approval")["ap_clone01"]
    s, b = wsx.post("/goals/g_coding01/approvals/ap_clone01/decide", {"choice": "goal", "hash": ap["payload_hash"]})
    assert s == 200 and b["status"] == "approved"
    wait_for(lambda: sess.waiting_for() == "approval:ap_push01", "second hold")
    resolved = entities(routes.ws().log.read("g_coding01"), "approval")["ap_clone01"]
    assert (resolved["status"], resolved["decision"], resolved["decided_by"]) == ("approved", "goal", "luke@test")


def push_hash(c):
    return entities(routes.ws().log.read("g_coding01"), "approval")["ap_push01"]["payload_hash"]


def get_to_push(c):
    start(c, "g_coding01")
    sess = routes.ws().sessions["g_coding01"]
    wait_for(lambda: sess.waiting_for() == "approval:ap_clone01", "hold")
    h = entities(routes.ws().log.read("g_coding01"), "approval")["ap_clone01"]["payload_hash"]
    assert c.post("/goals/g_coding01/approvals/ap_clone01/decide", {"choice": "once", "hash": h})[0] == 200
    wait_for(lambda: sess.waiting_for() == "approval:ap_push01", "push hold")
    return sess


def test_an_approval_cannot_be_reused_or_replayed(wsx):
    start(wsx, "g_coding01")
    wait_for(lambda: routes.ws().sessions["g_coding01"].waiting_for(), "hold")
    h = entities(routes.ws().log.read("g_coding01"), "approval")["ap_clone01"]["payload_hash"]
    body = {"choice": "once", "hash": h}
    assert wsx.post("/goals/g_coding01/approvals/ap_clone01/decide", body)[0] == 200
    s, b = wsx.post("/goals/g_coding01/approvals/ap_clone01/decide", body)
    assert s == 409 and "single-use" in b["error"]
    s, b = wsx.post("/goals/g_coding01/approvals/ap_clone01/decide", {"choice": "deny", "hash": h})
    assert s == 409


def test_a_stale_or_wrong_hash_is_refused_and_nothing_is_decided(wsx):
    start(wsx, "g_coding01")
    wait_for(lambda: routes.ws().sessions["g_coding01"].waiting_for(), "hold")
    for bad in ("", "0" * 64, "x"):
        s, b = wsx.post("/goals/g_coding01/approvals/ap_clone01/decide", {"choice": "once", "hash": bad})
        assert s == 409 and "changed" in b["error"]
    assert entities(routes.ws().log.read("g_coding01"), "approval")["ap_clone01"]["status"] == "pending"
    assert not [e for e in routes.ws().log.read("g_coding01") if e["type"] == "approval" and e["data"]["status"] != "pending"]


def test_high_risk_needs_a_second_press_enforced_by_the_server(wsx):
    sess = get_to_push(wsx)
    h = push_hash(wsx)
    s, b = wsx.post("/goals/g_coding01/approvals/ap_push01/decide", {"choice": "once", "hash": h})
    assert s == 428 and b["needs_confirm"]
    s, b = wsx.post("/goals/g_coding01/approvals/ap_push01/decide", {"choice": "goal", "hash": h})
    assert s == 428
    assert entities(routes.ws().log.read("g_coding01"), "approval")["ap_push01"]["status"] == "pending"
    assert sess.waiting_for() == "approval:ap_push01"
    s, b = wsx.post("/goals/g_coding01/approvals/ap_push01/decide", {"choice": "once", "hash": h, "confirmed": True})
    assert s == 200
    wait_for(lambda: routes.ws().log.finished("g_coding01"), "run to finish")
    assert last_status(wsx, "g_coding01")["data"]["status"] == "done"


def test_denying_a_high_risk_push_needs_no_second_press_and_nothing_leaves(wsx):
    get_to_push(wsx)
    h = push_hash(wsx)
    s, b = wsx.post("/goals/g_coding01/approvals/ap_push01/decide", {"choice": "deny", "hash": h, "note": "Not yet"})
    assert s == 200 and b["status"] == "denied"
    wait_for(lambda: routes.ws().log.finished("g_coding01"), "deny path")
    evs = routes.ws().log.read("g_coding01")
    assert not [e for e in evs if e["type"] == "tool_call" and e["data"]["name"] == "git.push"], "a denied push must never run"
    assert last_status(wsx, "g_coding01")["data"]["summary"].startswith("Fix verified in the sandbox. Push was denied")
    ap = entities(evs, "approval")["ap_push01"]
    assert (ap["status"], ap["decision"], ap["note"]) == ("denied", "deny", "Not yet")


def test_choice_must_be_one_of_the_three(wsx):
    start(wsx, "g_coding01")
    wait_for(lambda: routes.ws().sessions["g_coding01"].waiting_for(), "hold")
    h = entities(routes.ws().log.read("g_coding01"), "approval")["ap_clone01"]["payload_hash"]
    for bad in ("always", "", None, "approve"):
        assert wsx.post("/goals/g_coding01/approvals/ap_clone01/decide", {"choice": bad, "hash": h})[0] == 422


def test_decisions_are_audited_through_the_gate(wsx):
    start(wsx, "g_coding01")
    wait_for(lambda: routes.ws().sessions["g_coding01"].waiting_for(), "hold")
    h = entities(routes.ws().log.read("g_coding01"), "approval")["ap_clone01"]["payload_hash"]
    wsx.post("/goals/g_coding01/approvals/ap_clone01/decide", {"choice": "once", "hash": h})
    rows = [json.loads(ln) for ln in (wsx.dir / "ws" / "g_coding01.audit.ndjson").read_text().splitlines()]
    assert rows[-1]["event"] == "human_decision"
    assert rows[-1]["approval_id"] == "ap_clone01" and rows[-1]["payload_hash"] == h and rows[-1]["by"] == "luke@test"
    # the granted approval is consumable through the single send path — a
    # worker's call then writes cloud_call_allowed (the send itself is the
    # caller's, so the replay fixture injects a spy sender)
    from services.gate.gate import Approval
    q = routes.ws().queue("g_coding01", write=True)
    item = q.get("ap_clone01")
    res = q.gate.request_cloud_call(item["payload"], Approval(id="ap_clone01", payload_hash=h),
                                    sender=lambda p: len(str(p)))
    assert res.allowed
    rows = [json.loads(ln) for ln in (wsx.dir / "ws" / "g_coding01.audit.ndjson").read_text().splitlines()]
    assert rows[-1]["event"] == "cloud_call_allowed" and rows[-1]["approval_id"] == "ap_clone01"


def test_the_cap_pauses_instead_of_cancelling_and_resume_needs_a_real_cap(wsx):
    start(wsx, "g_cap01")
    sess = routes.ws().sessions["g_cap01"]
    wait_for(lambda: sess.waiting_for() == "resume", "cap hold")
    evs = routes.ws().log.read("g_cap01")
    assert evs[-1]["type"] == "run" and (evs[-1]["data"]["status"], evs[-1]["data"]["reason"]) == ("paused", "cap")
    assert not routes.ws().log.finished("g_cap01")
    assert wsx.post("/goals/g_cap01/resume", {"cap_usd": "abc"})[0] == 422
    assert wsx.post("/goals/g_cap01/resume", {"cap_usd": 0.5})[0] == 422, "a cap at or below spend can't resume"
    s, b = wsx.post("/goals/g_cap01/resume", {"cap_usd": 3.0})
    assert s == 200
    wait_for(lambda: routes.ws().log.finished("g_cap01"), "resumed run")
    costs = [e["data"] for e in routes.ws().log.read("g_cap01") if e["type"] == "cost"]
    assert costs[-1]["cap_usd"] == 3.0, "later cost events carry the new cap"
    assert wsx.post("/goals/g_cap01/resume", {"cap_usd": 9})[0] == 409, "not paused any more"


def test_stop_cancels_and_keeps_the_sandbox(wsx):
    start(wsx, "g_cap01")
    wait_for(lambda: routes.ws().sessions["g_cap01"].waiting_for(), "hold")
    assert wsx.post("/goals/g_cap01/stop")[0] == 200
    last = last_status(wsx, "g_cap01")
    assert last["data"]["status"] == "cancelled" and "sandbox was kept" in last["data"]["summary"]
    assert wsx.post("/goals/g_cap01/stop")[0] == 409


def test_messages_become_user_notes_and_empty_ones_are_refused(wsx):
    start(wsx, "g_coding01")
    wait_for(lambda: routes.ws().sessions["g_coding01"].waiting_for(), "hold")
    assert wsx.post("/goals/g_coding01/messages", {"text": "  "})[0] == 422
    assert wsx.post("/goals/g_coding01/messages", {"text": "use pytest-repeat", "to": "step_fix"})[0] == 200
    note = [e for e in routes.ws().log.read("g_coding01") if e["type"] == "note"][-1]
    assert note["data"]["role"] == "user" and note["data"]["to"] == "step_fix" and note["actor"]["name"] == "luke@test"


def test_new_goal_in_replay_plays_a_recording_and_says_so(wsx):
    s, b = wsx.post("/workspace/new", {"text": "fix the flaky test"})
    assert s == 200 and b["id"] == "g_coding01" and "not sent to any model" in b["notice"]
    assert wsx.post("/workspace/new", {"text": ""})[0] == 422


def test_new_goal_without_an_orchestrator_is_501(tmp_path, monkeypatch):
    monkeypatch.delenv("OC_REPLAY", raising=False)
    monkeypatch.setenv("OC_WORKSPACE_DIR", str(tmp_path / "ws"))
    monkeypatch.setattr(server.Handler, "auth", FakeAuth())
    routes.reset()
    srv = server.make_server(0)
    threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.01), daemon=True).start()
    c = Client(srv.server_address[1])
    try:
        s, b = c.post("/workspace/new", {"text": "do a thing"})
        assert s == 501 and "orchestrator" in b["error"]
        assert c.json("/goals.json") == {"goals": [], "replay": False}
    finally:
        routes.reset()
        srv.shutdown()


# ---- artifacts ------------------------------------------------------------------------------------------------------------


def played(c, goal="g_research01"):
    start(c, goal, hold=False)
    wait_for(lambda: routes.ws().log.finished(goal), "recording")


def test_html_artifact_is_served_sandboxed_with_no_network(wsx):
    played(wsx)
    s, h, body = wsx.get("/goals/g_research01/artifacts/art_html")
    csp = h["Content-Security-Policy"]
    assert s == 200 and "sandbox allow-scripts" in csp and "default-src 'none'" in csp and "connect-src" not in csp
    assert "allow-same-origin" not in csp and "allow-top-navigation" not in csp and "allow-forms" not in csp
    assert h["X-Frame-Options"] == "SAMEORIGIN" and h["X-Content-Type-Options"] == "nosniff"
    assert "<iframe" not in body


def test_the_workspace_page_allows_only_same_origin_frames(wsx):
    s, h, _ = wsx.get("/workspace")
    csp = h["Content-Security-Policy"]
    assert "frame-src 'self'" in csp and "script-src 'self'" in csp and "connect-src 'self'" in csp
    assert "unsafe-inline" not in csp and "http" not in csp.replace("'self'", "")


def test_text_artifacts_are_never_rendered_as_active_content(wsx):
    played(wsx)
    for art in ("art_report", "art_csv"):
        _, h, _ = wsx.get(f"/goals/g_research01/artifacts/{art}")
        assert h["Content-Type"].startswith("text/plain")
    _, h, _ = wsx.get("/goals/g_research01/artifacts/art_chart")
    assert "sandbox" in h["Content-Security-Policy"] and h["Content-Type"] == "image/svg+xml"


def test_export_of_a_gated_artifact_needs_an_approved_export_request(wsx):
    played(wsx)
    path = "/goals/g_research01/artifacts/art_pdf"
    assert wsx.get(path + "?download=1")[0] == 403
    assert wsx.get(path)[0] == 200, "viewing is not exporting"
    s, b = wsx.post(path + "/export")
    assert s == 200 and b["status"] == "pending"
    assert wsx.post(path + "/export")[1]["status"] == "pending", "asking twice doesn't make a second request"
    ap = entities(routes.ws().log.read("g_research01"), "approval")["ap_exp_art_pdf"]
    assert ap["payload"]["name"] == "report.pdf" and re.fullmatch(r"[0-9a-f]{64}", ap["payload"]["sha256"])
    assert wsx.get(path + "?download=1")[0] == 403
    assert wsx.post("/goals/g_research01/approvals/ap_exp_art_pdf/decide", {"choice": "once", "hash": ap["payload_hash"]})[0] == 200
    s, h, _ = wsx.get(path + "?download=1")
    assert s == 200 and h["Content-Disposition"].startswith("attachment")
    assert wsx.post("/goals/g_research01/artifacts/art_csv/export")[0] == 409, "no approval needed for this one"


def test_the_stream_stays_open_while_an_export_approval_is_pending_after_the_run_ended(wsx):
    played(wsx)
    assert wsx.post("/goals/g_research01/artifacts/art_pdf/export")[0] == 200
    evs, _ = wsx.sse("/goals/g_research01/events", headers={"Last-Event-ID": "59"}, timeout=1.2)
    assert [e["type"] for _, _, e in evs] == ["approval"] and not any(n == "end" for _, n, _ in evs), "an unanswered approval keeps the stream open"
    ap = entities(routes.ws().log.read("g_research01"), "approval")["ap_exp_art_pdf"]
    wsx.post("/goals/g_research01/approvals/ap_exp_art_pdf/decide", {"choice": "deny", "hash": ap["payload_hash"]})
    evs, _ = wsx.sse("/goals/g_research01/events", headers={"Last-Event-ID": "59"})
    assert evs[-1][1] == "end" and [e["data"]["status"] for _, _, e in evs[:-1]] == ["pending", "denied"]


def test_archive_listing_and_bad_refs(wsx):
    played(wsx, "g_coding01")
    j = wsx.json("/goals/g_coding01/artifacts/art_zip/listing")
    assert j["total"] == 4 and j["entries"][0]["path"].startswith("api-gateway/")
    assert wsx.get("/goals/g_coding01/artifacts/art_patch/listing")[0] == 404
    for bad in ("/goals/g_coding01/artifacts/..%2f..%2fserver", "/goals/g_coding01/artifacts/nope", "/goals/g_coding01/artifacts/art_zip%00"):
        assert wsx.get(bad)[0] in (404, 302), bad


def test_goal_list_reports_the_server_fold(wsx):
    j = wsx.json("/goals.json")
    assert [g["id"] for g in j["goals"]] == ["g_cap01", "g_coding01", "g_research01"] and j["replay"] is True
    assert all(g["replay"]["available"] and not g["replay"]["started"] for g in j["goals"])
    played(wsx)
    g = next(x for x in wsx.json("/goals.json")["goals"] if x["id"] == "g_research01")
    assert (g["status"], g["group"], g["artifacts"], g["sources"]) == ("done", "done", 5, 6) and g["cap_usd"] == 2.0


def test_the_page_renders_for_a_goal_and_embeds_the_workspace_actions(wsx):
    s, h, html = wsx.get("/workspace/g_research01")
    assert s == 200 and 'data-replay="1"' in html and '"goal": "g_research01"' in html
    assert re.search(r'role="tab"', html) and html.count('role="tabpanel"') == 7
    ids = json.loads(re.search(r'id="oc-actions">(.*?)</script>', html, re.S).group(1).replace("<\\/", "</"))
    assert {"ws.send", "ws.stop", "ws.allow_once", "ws.tab.cost", "nav.workspace"} <= {a["id"] for a in ids}
    assert "run.new" not in {a["id"] for a in ids}, "c creates a goal here, not a matrix run"


def test_the_workspace_does_not_shadow_the_console_goals_pages(wsx):
    assert wsx.get("/goals")[0] == 200 and wsx.get("/goals/new")[0] == 200
    _, _, html = wsx.get("/workspace")
    assert 'data-page="workspace"' in html and 'data-page="goals"' not in html


def test_goals_page_has_no_inline_script_or_style(wsx):
    _, _, html = wsx.get("/workspace")
    assert not re.search(r"<script(?![^>]*(src=|type=\"application/json\"))", html)
    assert " style=" not in html and "<style" not in html


def test_memory_log_reset_lets_replay_restart(wsx):
    start(wsx, "g_research01", hold=False)
    wait_for(lambda: routes.ws().log.finished("g_research01"), "first run")
    start(wsx, "g_research01", hold=False, restart=True)
    wait_for(lambda: routes.ws().log.finished("g_research01"), "second run")
    assert routes.ws().log.last_seq("g_research01") == 59 and isinstance(routes.ws().log, MemoryLog)


def test_a_replay_restart_can_decide_the_same_approval_again(wsx):
    for restart in (False, True, True):
        start(wsx, "g_coding01", restart=restart)
        wait_for(lambda: routes.ws().sessions["g_coding01"].waiting_for(), "hold")
        h = entities(routes.ws().log.read("g_coding01"), "approval")["ap_clone01"]["payload_hash"]
        s, b = wsx.post("/goals/g_coding01/approvals/ap_clone01/decide", {"choice": "once", "hash": h})
        assert s == 200, b
