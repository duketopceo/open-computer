"""The workbench (/work): three-pane workplace surface — composer, live plan
tree, inspector. The fragment endpoint feeds work.js; the goal composer flows
through the same confirm contract as the HTML path, JSON instead of pages.
"""

import json

import pytest

from services.opsview import goalruns, server


@pytest.fixture
def submitted(monkeypatch):
    rec = []

    def fake(goal, who):
        rec.append((goal, who))
        return "goal_ab12cd34"

    monkeypatch.setattr(server, "_submit_goal", fake)
    return rec


def post_json(console, path, data, cookie=None, token=None):
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["X-CSRF-Token"] = token
    if cookie:
        headers["Cookie"] = cookie
    return console.request("POST", path, body=json.dumps(data), headers=headers, cookie=cookie is None)


def test_work_page_redirects_to_the_react_shell(console):
    status, headers, _ = console.get("/work")
    assert status == 302 and headers["Location"] == "/"


def test_frag_work_payload_shape(console):
    status, headers, body = console.get("/_frag/work")
    assert status == 200 and "application/json" in headers["Content-Type"]
    d = json.loads(body)
    assert set(d) == {"rail", "main", "inspect", "active"}
    assert "w-hero" in d["main"]  # nothing selected -> the hero prompt


def test_frag_work_run_detail_and_subtask(console, monkeypatch, tmp_path):
    import time

    monkeypatch.setenv("OC_GOALS_DIR", str(tmp_path))
    rid = goalruns.submit(
        "Do one thing",
        lambda prompt: '[{"id":"a","title":"first task","prompt":"p1"}]',
        lambda task: "result-body",
        by="t",
    )
    for _ in range(200):
        got = goalruns.get_run(rid)
        if got and got["subtasks"] and got["subtasks"][0]["state"] in ("DONE", "FAILED"):
            break
        time.sleep(0.05)
    status, _, body = console.get(f"/_frag/work?run={rid}&sub=0")
    assert status == 200
    d = json.loads(body)
    assert "Do one thing" in d["main"] and "first task" in d["main"]
    assert "tl-step" in d["main"] and "Planned 1 step" in d["main"]  # narrative timeline
    assert rid in d["rail"]
    assert "result-body" in d["inspect"]
    assert "w-doc" in d["inspect"] and "Details" in d["inspect"]  # doc result + disclosure


def test_goal_json_flow_needs_confirm_then_runs(console, submitted):
    status, _, body = post_json(console, "/goals", {"goal": "Summarise the audit log"}, token=console.csrf())
    assert status == 200
    d = json.loads(body)
    assert d["needs_confirm"] is True and "cap" in d and submitted == []
    status, _, body = post_json(
        console, "/goals", {"goal": "Summarise the audit log", "confirm": "1"}, token=console.csrf()
    )
    assert status == 200 and json.loads(body)["run"] == "goal_ab12cd34"
    assert submitted == [("Summarise the audit log", "luke@test")]


def test_goal_json_rejects_empty(console, submitted):
    status, _, body = post_json(console, "/goals", {"goal": "   ", "confirm": "1"}, token=console.csrf())
    assert status == 400 and submitted == []


def test_first_loopback_page_csrf_posts_cleanly(console):
    """The first loopback response mints the session cookie — its csrf token
    must be bound to that minted cookie, or the first POST 403s."""
    status, headers, body = console.get("/api/session", cookie=False)
    assert status == 200
    cookie = headers["Set-Cookie"].split(";", 1)[0]
    token = json.loads(body)["csrf"]
    status, _, body = post_json(console, "/goals", {"goal": "probe"}, cookie=cookie, token=token)
    assert status == 200 and json.loads(body).get("needs_confirm"), body


def _run(**sub):
    base = {"subtask": "a", "title": "first", "state": "DONE", "engine": "or", "placement": "cloud-OpenRouter",
            "reason": "", "ts": 100.0, "started_ts": 110.0, "finished_ts": 114.2, "result": "out"}
    return {"run": "goal_00000001", "goal": "g", "subtasks": [{**base, **sub}], "declared": 1,
            "worker_failures": [], "started": 99.0, "ended": 115.0}


def test_step_duration_runs_from_claim_not_dispatch():
    from services.opsview import views

    run = _run()
    assert "4.2s" in views.work_main(run, 200.0) and "14s" not in views.work_main(run, 200.0)
    assert "4.2s" in views.page_goal_detail(run, 200.0)
    inspector = views.work_inspect(run, "0", [], 200.0)
    assert inspector.count("4.2s") == 2  # step line and Details row agree


def test_step_that_never_ran_shows_no_duration():
    from services.opsview import views

    run = _run(state="FAILED", error="dep 'x' failed — cannot run")
    del run["subtasks"][0]["started_ts"]
    assert "Duration" not in views.work_inspect(run, "0", [], 200.0)
    assert "14s" not in views.work_main(run, 200.0) and "14.2s" not in views.page_goal_detail(run, 200.0)


def test_running_step_shows_elapsed_since_claim():
    from services.opsview import views

    run = _run(state="LEASED", finished_ts=None, result=None)
    assert "Working on cloud-OpenRouter · 5.0s" in views.work_main(run, 115.0)


def test_thinking_renders_escaped_and_collapsed():
    from services.opsview import views

    run = _run(thinking="<script>alert(1)</script> because")
    run["plan_thinking"] = "plan <b>why</b>"
    inspector = views.work_inspect(run, "0", [], 200.0)
    assert "<script>alert" not in inspector and "&lt;script&gt;alert(1)&lt;/script&gt; because" in inspector
    assert '<details class="w-details w-thinking"><summary>Thinking</summary>' in inspector
    main = views.work_main(run, 200.0)
    assert "plan &lt;b&gt;why&lt;/b&gt;" in main and "<b>why" not in main


def test_no_thinking_no_toggle():
    from services.opsview import views

    run = _run()
    assert "Thinking" not in views.work_inspect(run, "0", [], 200.0) + views.work_main(run, 200.0)


def test_failed_step_error_is_escaped_in_the_inspector_narrative():
    from services.opsview import views

    run = _run(state="FAILED", error="boom <img src=x onerror=alert(1)>", result=None)
    inspector = views.work_inspect(run, "0", [], 200.0)
    assert "<img" not in inspector and "&lt;img src=x onerror=alert(1)&gt;" in inspector
    assert "<img" not in views.work_main(run, 200.0)
