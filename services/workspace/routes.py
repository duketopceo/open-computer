"""Workspace routes: the page, the SSE event stream, artifacts, and the few POSTs that steer a goal.

Wired into services/opsview/server.py (same session, CSRF, Origin and CSP rules). Safety rules hold here too:
no GET writes anything (replay is started by a POST), every POST needs session + CSRF + same-origin,
and anything that leaves the box goes through an approval bound to its payload hash.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import zipfile
from pathlib import Path
from urllib.parse import unquote

from oc.events import entities, fold
from services.gate.queue import QueueError

from .replay import FIXTURES, Workspace

STATIC = Path(__file__).resolve().parent / "static"
STATIC_TYPES = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
WS_CSP = ("default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self' data:; font-src 'self'; "
          "connect-src 'self'; frame-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
GOAL = r"(?P<goal>g_[a-z0-9_]+)"
REF = r"(?P<ref>[a-z]+_[a-z0-9_]+)"

GET_ROUTES = [
    (r"/workspace", "g_page"), (rf"/workspace/{GOAL}", "g_page"), (r"/goals\.json", "g_list"),
    (rf"/goals/{GOAL}/events", "g_events"), (rf"/goals/{GOAL}/state", "g_state"),
    (rf"/goals/{GOAL}/approvals", "g_approvals"),
    (rf"/goals/{GOAL}/artifacts/{REF}", "g_artifact"), (rf"/goals/{GOAL}/artifacts/{REF}/listing", "g_listing"),
]
POST_ROUTES = [
    (r"/workspace/new", "p_new"), (rf"/goals/{GOAL}/messages", "p_message"), (rf"/goals/{GOAL}/stop", "p_stop"),
    (rf"/goals/{GOAL}/resume", "p_resume"), (rf"/goals/{GOAL}/replay", "p_replay"),
    (rf"/goals/{GOAL}/approvals/{REF}/decide", "p_decide"),
    (rf"/goals/{GOAL}/artifacts/{REF}/export", "p_export"),
    (rf"/goals/{GOAL}/steps/{REF}/stop", "p_step_stop"),
]

_WS: Workspace | None = None


def ws() -> Workspace:
    global _WS
    if _WS is None:
        state = Path(os.environ.get("OC_WORKSPACE_DIR") or tempfile.mkdtemp(prefix="oc-workspace-"))
        _WS = Workspace(replay=os.environ.get("OC_REPLAY") == "1", state_dir=state,
                        events_dir=Path(os.environ["OC_EVENTS_DIR"]) if os.environ.get("OC_EVENTS_DIR") else None)
    return _WS


def reset() -> None:
    """Tests: drop the singleton so the next request re-reads the environment."""
    global _WS
    if _WS:
        for s in _WS.sessions.values():
            s.stop()
    _WS = None


def match(table, path):
    for pat, name in table:
        m = re.fullmatch(pat, path)
        if m:
            return name, m.groupdict()
    return None, {}


def is_ws_path(path: str) -> bool:
    return match(GET_ROUTES, path)[0] is not None or match(POST_ROUTES, path)[0] is not None


# ---------------------------------------------------------------- static


def serve_static(h, path: str) -> bool:
    if not path.startswith("/static/ws/"):
        return False
    name = path[len("/static/ws/"):]
    f = STATIC / name
    if "/" in name or ".." in name or not f.is_file() or f.suffix not in STATIC_TYPES:
        h._send(404, b"not found", "text/plain")
        return True
    h._send(200, f.read_bytes(), STATIC_TYPES[f.suffix] + "; charset=utf-8", {"Cache-Control": "no-cache"})
    return True


# ---------------------------------------------------------------- GET


def handle_get(h, path: str, qs: dict) -> bool:
    name, args = match(GET_ROUTES, path)
    if not name:
        return False
    globals()[name](h, qs, **args)
    return True


def _summary(w: Workspace, goal: str) -> dict:
    evs = w.log.read(goal)
    if evs:
        s = fold(evs)
    else:
        rec = w.recordings.get(goal)
        s = {"id": goal, "title": rec["meta"]["title"] if rec else goal, "status": "queued", "reason": None,
             "spent_usd": 0.0, "cap_usd": 0.0, "pending_approvals": 0, "artifacts": 0, "sources": 0, "changes": 0,
             "last_seq": 0, "updated": 0.0, "started": None, "group": "running"}
    sess = w.sessions.get(goal)
    s["replay"] = {"available": goal in w.recordings, "started": sess is not None,
                   "state": sess.state if sess else "idle", "summary": (w.recordings.get(goal) or {}).get("meta", {}).get("summary", "")}
    return s


def g_list(h, qs):
    w = ws()
    goals = [_summary(w, g) for g in w.goal_ids()]
    h._json(200, {"goals": goals, "replay": w.replay})


def g_state(h, qs, goal):
    w = ws()
    if not w.known(goal):
        return h._json(404, {"error": "No such goal."})
    h._json(200, _summary(w, goal))


def g_approvals(h, qs, goal):
    w = ws()
    if not w.known(goal):
        return h._json(404, {"error": "No such goal."})
    items = [{"id": a["id"], "status": a["status"], "payload_hash": a["payload_hash"], "risk": a["risk"]}
             for a in entities(w.log.read(goal), "approval").values()]
    h._json(200, {"approvals": items})


def g_page(h, qs, goal=None):
    from . import page
    w = ws()
    if goal and not w.known(goal):
        return h._send(404, page.render(h, None, 404).encode(), page=True, csp=WS_CSP)
    h._send(200, page.render(h, goal).encode(), page=True, csp=WS_CSP)


def g_events(h, qs, goal):
    """Server-sent events. `Last-Event-ID` (header, or ?last_event_id=) resumes after that seq."""
    w = ws()
    if not w.known(goal):
        return h._json(404, {"error": "No such goal."})
    raw = h.headers.get("Last-Event-ID") or (qs.get("last_event_id") or [""])[0]
    try:
        after = max(int(raw), 0)
    except ValueError:
        after = 0
    hb = float((qs.get("hb") or ["15"])[0])
    h.send_response(200)
    for k, v in (("Content-Type", "text/event-stream; charset=utf-8"), ("Cache-Control", "no-store"),
                 ("X-Accel-Buffering", "no"), ("X-Content-Type-Options", "nosniff")):
        h.send_header(k, v)
    h.end_headers()

    def out(text: str) -> bool:
        try:
            h.wfile.write(text.encode())
            h.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False
    if not out("retry: 2000\n\n"):
        return
    while True:
        last = w.log.last_seq(goal)
        if after > last:  # the stream was rewound (replay restart): tell the client to start over
            if not out("event: reset\ndata: {}\n\n"):
                return
            after = 0
        for ev in w.log.read(goal, after):
            if not out(f"id: {ev['seq']}\ndata: {json.dumps(ev, separators=(',', ':'))}\n\n"):
                return
            after = ev["seq"]
        sess = w.sessions.get(goal)
        done_replay = sess is None or sess.state in ("complete", "stopped")
        waiting_on_human = any(a["status"] == "pending" for a in entities(w.log.read(goal), "approval").values())
        if w.log.finished(goal) and w.log.last_seq(goal) == after and (not w.replay or done_replay) and not waiting_on_human:
            out("event: end\ndata: {}\n\n")
            return
        if not w.log.wait(goal, after, hb) and not out(": hb\n\n"):
            return


# ---------------------------------------------------------------- artifacts


def _artifact(w: Workspace, goal: str, ref: str):
    art = entities(w.log.read(goal), "artifact").get(ref)
    if not art:
        return None, None
    if w.replay:
        rec = w.recordings.get(goal)
        name = rec["meta"]["files"].get(ref) if rec else None
        f = (rec["dir"] / name) if name else None
    else:
        f = w.state_dir / "artifacts" / goal / ref
    return art, f if f and f.is_file() else None


def _export_ok(w: Workspace, goal: str, art: dict) -> bool:
    ex = art.get("export") or {}
    if not ex.get("requires_approval"):
        return True
    ap = entities(w.log.read(goal), "approval").get(f"ap_exp_{art['id']}")
    return bool(ap and ap["status"] == "approved")


ARTIFACT_CSP = {
    # Sandboxed, scripts allowed, nothing can touch the network or the console's origin.
    "html": "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'",
    "svg": "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'",
    "pdf": "default-src 'none'; frame-ancestors 'self'; object-src 'self'",
}
INLINE_TEXT = "text/plain; charset=utf-8"


def g_artifact(h, qs, goal, ref):
    w = ws()
    art, f = _artifact(w, goal, ref)
    if not art or not f:
        return h._json(404, {"error": "No such artifact."})
    download = (qs.get("download") or [""])[0] == "1"
    if download and not _export_ok(w, goal, art):
        return h._json(403, {"error": "Exporting this needs an approval. Approve to export it first."})
    kind = art["kind"]
    ctype = art.get("mime") or "application/octet-stream"
    if kind in ("markdown", "code", "text", "csv", "diff", "mermaid") and not download:
        ctype = INLINE_TEXT  # never let the browser render these as active content
    elif kind == "html" and not download:
        ctype = "text/html; charset=utf-8"
    extra = {"Content-Security-Policy": ARTIFACT_CSP.get(kind, "default-src 'none'; sandbox; frame-ancestors 'self'"),
             "X-Frame-Options": "SAMEORIGIN", "Cache-Control": "no-store",
             "Content-Disposition": ("attachment" if download else "inline") + f'; filename="{art["name"]}"'}
    h._send(200, f.read_bytes(), ctype, extra)


def g_listing(h, qs, goal, ref):
    w = ws()
    art, f = _artifact(w, goal, ref)
    if not art or not f or art["kind"] != "archive":
        return h._json(404, {"error": "No such archive."})
    try:
        with zipfile.ZipFile(f) as z:
            entries = [{"path": i.filename, "size": i.file_size, "dir": i.is_dir()} for i in z.infolist()]
    except zipfile.BadZipFile:
        return h._json(422, {"error": "This archive can't be listed. Download it instead."})
    h._json(200, {"entries": entries, "total": len(entries), "bytes": f.stat().st_size})


# ---------------------------------------------------------------- POST


def handle_post(h, who: str, body: dict, path: str) -> bool:
    name, args = match(POST_ROUTES, path)
    if not name:
        return False
    globals()[name](h, who, body, **args)
    return True


def _need_goal(h, goal):
    w = ws()
    if not w.known(goal):
        h._json(404, {"error": "No such goal."})
        return None
    return w


def p_new(h, who, body):
    text = str(body.get("text", "")).strip()[:2000]
    if not text:
        return h._json(422, {"error": "Describe the goal first."})
    w = ws()
    if not w.replay:
        return h._json(501, {"error": "No orchestrator is connected yet, so goals can't be started from here."})
    low = text.lower()
    pick = ("g_cap01" if re.search(r"cap|spend|budget|vendor|deep|thorough", low)
            else "g_coding01" if re.search(r"fix|bug|test|code|push|flaky|pr\b|refactor", low) else "g_research01")
    h._json(200, {"id": pick, "replay": True,
                  "notice": "Replay mode: this plays a recorded goal. Your text was not sent to any model."})


def p_replay(h, who, body, goal):
    w = _need_goal(h, goal)
    if not w:
        return
    if not w.replay or goal not in w.recordings:
        return h._json(409, {"error": "Replay is off for this goal."})
    action = body.get("action", "start")
    try:
        speed = float(body.get("speed", 4))
    except (TypeError, ValueError):
        return h._json(422, {"error": "speed must be a number."})
    hold = str(body.get("hold", "1")) not in ("0", "false", "False")
    s = w.start_replay(goal, speed=max(speed, 0.0), hold=hold, restart=(action == "restart"))
    h._json(200, {"ok": True, "state": s.state, "speed": s.speed, "hold": s.hold})


def p_message(h, who, body, goal):
    w = _need_goal(h, goal)
    if not w:
        return
    text = str(body.get("text", "")).strip()[:4000]
    if not text:
        return h._json(422, {"error": "Write a message first."})
    to = body.get("to")
    em = w.emitter(goal, {"name": who, "kind": "human"})
    n = w.log.last_seq(goal) + 1
    data = {"id": f"note_u{n}", "role": "user", "kind": "message", "text": text}
    if to and re.fullmatch(r"[a-z]+_[a-z0-9_]+", str(to)):
        data["to"] = to
    em.emit("note", data)
    h._json(200, {"ok": True, "queued": bool(w.sessions.get(goal) and w.sessions[goal].state == "playing")})


def p_step_stop(h, who, body, goal, ref):
    w = _need_goal(h, goal)
    if not w:
        return
    n = w.log.last_seq(goal) + 1
    w.emitter(goal, {"name": "console", "kind": "system"}).note(
        f"note_s{n}", "system", f"Stop requested for {ref} by {who}." + (" Replay mode: nothing is running, so nothing stopped." if w.replay else ""), kind="status")
    h._json(200, {"ok": True})


def p_stop(h, who, body, goal):
    w = _need_goal(h, goal)
    if not w:
        return
    if w.log.finished(goal):
        return h._json(409, {"error": "This goal already ended."})
    sess = w.sessions.get(goal)
    if sess:
        sess.stop()
        sess.thread.join(1)
    w.emitter(goal, {"name": who, "kind": "human"}).run("cancelled", summary="Stopped by the operator. The sandbox was kept.")
    h._json(200, {"ok": True})


def p_resume(h, who, body, goal):
    w = _need_goal(h, goal)
    if not w:
        return
    state = fold(w.log.read(goal))
    if not (state["status"] == "paused" and state["reason"] == "cap"):
        return h._json(409, {"error": "This goal isn't paused at its cap."})
    try:
        cap = float(body.get("cap_usd"))
    except (TypeError, ValueError):
        return h._json(422, {"error": "Enter the new cap in dollars."})
    if cap <= state["spent_usd"]:
        return h._json(422, {"error": f"The new cap must be above what's spent (${state['spent_usd']:.2f})."})
    w.emitter(goal, {"name": who, "kind": "human"}).run("running", cap_usd=cap)
    sess = w.sessions.get(goal)
    if sess:
        sess.release("resume", {"cap_usd": cap})
    h._json(200, {"ok": True, "cap_usd": cap})


def p_export(h, who, body, goal, ref):
    w = _need_goal(h, goal)
    if not w:
        return
    art, f = _artifact(w, goal, ref)
    if not art or not f:
        return h._json(404, {"error": "No such artifact."})
    if not (art.get("export") or {}).get("requires_approval"):
        return h._json(409, {"error": "This artifact can be exported without approval."})
    aid = f"ap_exp_{ref}"
    em = w.emitter(goal, {"name": "console", "kind": "system"})
    existing = entities(w.log.read(goal), "approval").get(aid)
    if existing:
        return h._json(200, {"id": aid, "status": existing["status"]})
    digest = hashlib.sha256(f.read_bytes()).hexdigest()
    payload = {"artifact": ref, "name": art["name"], "version": art["version"], "sha256": digest, "bytes": f.stat().st_size}
    ev = em.approval_request(aid, kind="export", action=f"Export {art['name']}", destination="download to this browser", payload=payload,
                             payload_preview=f"{art['name']}  v{art['version']}\n{f.stat().st_size} bytes\nsha256 {digest}",
                             why=f"You asked to take {art['name']} out of the box.", risk="low",
                             if_denied="The file stays in the sandbox.", scope_pattern=f"export {art['name']}")
    w.register_approval(goal, ev)
    h._json(200, {"id": aid, "status": "pending"})


def p_decide(h, who, body, goal, ref):
    w = _need_goal(h, goal)
    if not w:
        return
    ap = entities(w.log.read(goal), "approval").get(ref)
    if not ap:
        return h._json(404, {"error": "No such approval."})
    if ap["status"] != "pending":
        return h._json(409, {"error": f"Already {ap['status']}. Approvals are single-use."})
    choice = body.get("choice")
    if choice not in ("once", "goal", "deny"):
        return h._json(422, {"error": "Choose allow once, allow for this goal, or deny."})
    if choice != "deny" and ap["risk"] == "high" and not body.get("confirmed"):
        return h._json(428, {"error": "This is high risk. Press Allow once a second time to confirm.", "needs_confirm": True})
    seen = str(body.get("hash") or "")
    if seen != ap["payload_hash"]:
        return h._json(409, {"error": "The request changed since you opened it. Review it again."})
    approve = choice != "deny"
    note = str(body.get("note", "")).strip()[:1000]
    q = w.queue(goal, write=True)
    if q.get(ref) is None:  # live goals registered at emit time; this covers a restart between request and decision
        w.register_approval(goal, {"data": ap, "ts": time.time(), "actor": {"name": "agent"}})
    try:
        q.decide(ref, approve=approve, by=who, note=note or ("" if approve else "Denied by the operator."), expect_hash=seen)
    except QueueError as e:
        return h._json(e.status, {"error": str(e)})
    except ValueError as e:  # the gate refuses to decide an id twice; answer, don't drop the connection
        return h._json(409, {"error": str(e)})
    em = w.emitter(goal, {"name": who, "kind": "human"})
    em.approval_resolve(ref, "approved" if approve else "denied", decision=choice, decided_by=who, note=note or None)
    sess = w.sessions.get(goal)
    if sess:
        sess.release(f"approval:{ref}", {"deny": not approve})
    h._json(200, {"ok": True, "id": ref, "status": "approved" if approve else "denied", "decision": choice})


__all__ = ["FIXTURES", "GET_ROUTES", "POST_ROUTES", "handle_get", "handle_post", "is_ws_path", "reset", "serve_static", "unquote"]
