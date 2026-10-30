"""Every state-changing route is POST, needs a session, a CSRF token bound to
that session, and a same-origin Origin; spending and leaving-the-box actions
also need an explicit confirm."""

import json

import pytest

from services.opsview import server

from test_routing import concrete

POSTS = [concrete(p) for p, _ in server.POST_ROUTES]


def test_post_without_session_is_401(console, calls):
    remote = {"Host": "remote.test"}
    for path in POSTS:
        assert console.post(path, {"confirm": "1"}, csrf=False, cookie=False, headers=remote)[0] == 401
    assert calls == {"matrix": 0, "route": 0}


@pytest.mark.parametrize("bad", [False, "", "deadbeef", "0" * 64])
def test_post_without_valid_csrf_is_403(console, calls, bad):
    for path in POSTS:
        status, _, body = console.post(path, {"confirm": "1", "q": "x"}, csrf=bad)
        assert status == 403, path
        assert "CSRF" in body
    assert calls == {"matrix": 0, "route": 0}


def test_csrf_token_is_bound_to_the_session(console, calls):
    good = console.csrf()
    # same token, different cookie value: must not validate (token = HMAC of the cookie)
    status, _, _ = console.request(
        "POST",
        "/runs",
        body=f"csrf={good}&confirm=1",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Cookie": "oc_sess=other", "Host": "remote.test"},
        cookie=False,
    )
    assert status == 401  # FakeAuth rejects the other cookie before the token is even compared
    assert calls["matrix"] == 0


def test_cross_origin_post_refused_even_with_token(console, calls):
    status, _, body = console.post("/runs", {"confirm": "1"}, headers={"Origin": "https://evil.example"})
    assert status == 403 and "Cross-origin" in body
    assert calls["matrix"] == 0


def test_same_origin_post_with_token_and_confirm_runs_once(console, calls):
    origin = f"http://127.0.0.1:{console.port}"
    status, headers, _ = console.post("/runs", {"confirm": "1"}, headers={"Origin": origin})
    assert status == 303 and headers["Location"].startswith("/runs/run_")
    assert calls["matrix"] == 1
    status, _, html = console.get(headers["Location"])
    assert status == 200 and "R2" in html and "pending" in html.lower()


def test_run_requires_confirm_step(console, calls):
    status, _, body = console.post("/runs", {})
    assert status == 400 and "Confirm" in body
    assert calls["matrix"] == 0


def test_confirm_page_names_what_it_touches_and_costs(console):
    _, _, html = console.get("/runs/new")
    assert "microVMs" in html and "Model spend" in html and "$0.00" in html
    assert "127.0.0.1" in html  # destination: the control plane host
    assert 'method="post"' in html and 'name="csrf"' in html and 'name="confirm"' in html


def test_router_free_engine_routes_in_one_step(console, calls):
    status, _, body = console.post("/api/router/test", {"q": "summarise this"})
    result = json.loads(body)["result"]
    assert status == 200 and calls["route"] == 1
    assert result["engine"] == "table" and result["cost"] == 0.0


def test_router_paid_engine_needs_confirm_and_shows_destination(console, calls, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fixture-not-real")
    monkeypatch.setenv("OPENROUTER_BASE", "https://openrouter.ai/api")
    status, _, body = console.post("/api/router/test", {"q": "secret plan"})
    data = json.loads(body)
    assert status == 200 and calls["route"] == 0  # nothing left the box
    assert data == {"needs_confirm": True, "dest_host": "openrouter.ai", "cost": 0.000004}
    status, _, body = console.post("/api/router/test", {"q": "secret plan", "confirm": "1"})
    assert status == 200 and calls["route"] == 1 and "result" in json.loads(body)


def test_router_destination_is_in_the_api_before_any_click(console, monkeypatch):
    data = json.loads(console.get("/api/router")[2])
    assert data["paid"] is False and data["per_route_cost"] == 0
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fixture-not-real")
    data = json.loads(console.get("/api/router")[2])
    assert data["paid"] is True and data["dest_host"] == "openrouter.ai" and data["per_route_cost"] == 0.000004


def test_csp_forbids_inline_and_remote(console):
    _, h, html = console.get("/")
    csp = h["Content-Security-Policy"]
    assert "'unsafe-inline'" not in csp and "http" not in csp
    assert " style=" not in html and "<style" not in html
    import re

    assert not re.search(r"<script(?![^>]*(src=|type=\"application/json\"))", html)
