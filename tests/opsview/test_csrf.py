"""Every state-changing route is POST, needs a session, a CSRF token bound to
that session, and a same-origin Origin; spending and leaving-the-box actions
also need an explicit confirm."""

import pytest

from services.opsview import server

from test_routing import concrete

POSTS = [concrete(p) for p, _ in server.POST_ROUTES]


def test_post_without_session_is_401(console, calls):
    for path in POSTS:
        assert console.post(path, {"confirm": "1"}, csrf=False, cookie=False)[0] == 401
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
        "POST", "/runs", body=f"csrf={good}&confirm=1",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Cookie": "oc_sess=other"}, cookie=False)
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
    status, _, html = console.post("/router/test", {"q": "summarise this"})
    assert status == 200 and "Route result" in html and calls["route"] == 1
    assert "stays on this box" in html or "nothing leaves this box" in html


def test_router_paid_engine_needs_confirm_and_shows_destination(console, calls, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fixture-not-real")
    monkeypatch.setenv("OPENROUTER_BASE", "https://openrouter.ai/api")
    status, _, html = console.post("/router/test", {"q": "secret plan"})
    assert status == 200 and calls["route"] == 0
    assert "Send this to the decider?" in html and "openrouter.ai" in html and "$0.000004" in html
    assert "secret plan" in html
    status, _, html = console.post("/router/test", {"q": "secret plan", "confirm": "1"})
    assert status == 200 and calls["route"] == 1


def test_router_page_shows_destination_before_any_click(console, monkeypatch):
    _, _, html = console.get("/router")
    assert "nothing leaves this box" in html
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fixture-not-real")
    _, _, html = console.get("/router")
    assert "openrouter.ai" in html and "$0.000004" in html


def test_csp_forbids_inline_and_remote(console):
    _, h, html = console.get("/")
    csp = h["Content-Security-Policy"]
    assert "'unsafe-inline'" not in csp and "http" not in csp
    assert " style=" not in html and "<style" not in html
    import re
    assert not re.search(r"<script(?![^>]*(src=|type=\"application/json\"))", html)
