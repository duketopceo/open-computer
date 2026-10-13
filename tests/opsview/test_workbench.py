"""The workbench (/work): three-pane workplace surface — composer, live plan
tree, inspector. The fragment endpoint feeds work.js; the goal composer flows
through the same confirm contract as the HTML path, JSON instead of pages.
"""

import json
import re

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
    return console.request("POST", path, body=json.dumps(data), headers=headers,
                           cookie=cookie is None)


def test_work_page_renders_the_three_panes(console):
    status, _, html = console.get("/work")
    assert status == 200
    for pane in ('id="w-rail"', 'id="w-main"', 'id="w-inspect"', 'name="goal"', 'class="composer"'):
        assert pane in html
    assert "/static/work.js" in html
    _, _, other = console.get("/goals")
    assert "/static/work.js" not in other  # only the work page loads it


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
        lambda task: "result-body", by="t")
    for _ in range(200):
        got = goalruns.get_run(rid)
        if got and got["subtasks"] and got["subtasks"][0]["state"] in ("DONE", "FAILED"):
            break
        time.sleep(0.05)
    status, _, body = console.get(f"/_frag/work?run={rid}&sub=0")
    assert status == 200
    d = json.loads(body)
    assert "Do one thing" in d["main"] and "first task" in d["main"]
    assert rid in d["rail"]
    assert "result-body" in d["inspect"]


def test_goal_json_flow_needs_confirm_then_runs(console, submitted):
    status, _, body = post_json(console, "/goals", {"goal": "Summarise the audit log"},
                              token=console.csrf())
    assert status == 200
    d = json.loads(body)
    assert d["needs_confirm"] is True and "cap" in d and submitted == []
    status, _, body = post_json(console, "/goals",
                                {"goal": "Summarise the audit log", "confirm": "1"},
                                token=console.csrf())
    assert status == 200 and json.loads(body)["run"] == "goal_ab12cd34"
    assert submitted == [("Summarise the audit log", "luke@test")]


def test_goal_json_rejects_empty(console, submitted):
    status, _, body = post_json(console, "/goals", {"goal": "   ", "confirm": "1"},
                              token=console.csrf())
    assert status == 400 and submitted == []


def test_first_loopback_page_csrf_posts_cleanly(console):
    """The first loopback response mints the session cookie — its csrf token
    must be bound to that minted cookie, or the first POST 403s."""
    status, headers, html = console.get("/work", cookie=False)
    assert status == 200
    cookie = headers["Set-Cookie"].split(";", 1)[0]
    token = re.search(r'name="csrf-token" content="([0-9a-f]+)"', html).group(1)
    status, _, body = post_json(console, "/goals", {"goal": "probe"},
                                cookie=cookie, token=token)
    assert status == 200 and json.loads(body).get("needs_confirm"), body
