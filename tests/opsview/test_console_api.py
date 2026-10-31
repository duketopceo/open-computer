"""JSON data contract and SPA deep links for the ported console pages
(Devices, Sandboxes, Router, Audit). Fixture data only; spend paths are recorders."""

from __future__ import annotations

import json

import pytest

from services.opsview import consoleapi, server

SPA_PATHS = ["/devices", "/sandboxes", "/sandboxes/sbx-7f3a91c2", "/sandboxes/does-not-exist", "/router", "/audit"]


def j(console, path):
    status, headers, body = console.get(path)
    assert headers["Content-Type"].startswith("application/json"), path
    assert headers["Cache-Control"] == "no-store"
    return status, json.loads(body)


# ---------------------------------------------------------------- SPA shell


@pytest.mark.parametrize("path", SPA_PATHS + ["/audit?f=blocked&q=gpt"])
def test_ported_paths_serve_the_spa_shell(console, path):
    status, headers, html = console.get(path)
    assert status == 200 and 'id="root"' in html
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"
    assert "data-page" not in html  # not a server-rendered Silver Lining page


@pytest.mark.parametrize("path", SPA_PATHS)
def test_ported_paths_503_without_a_build_but_root_still_degrades(console, monkeypatch, tmp_path, path):
    monkeypatch.setattr(server, "UI_DIST", tmp_path / "no-dist")
    status, _, body = console.get(path)
    assert status == 503 and "pnpm --dir ui build" in body
    assert console.get("/")[0] == 200  # `/` keeps its server-rendered fallback


def test_ported_paths_are_behind_the_passkey_gate(console):
    remote = {"Host": "remote.test"}
    for path in [*SPA_PATHS, "/api/sandboxes", "/api/router", "/api/audit"]:
        status, headers, _ = console.get(path, cookie=False, headers=remote)
        assert status == 302 and headers["Location"] == "/login", path
    status, _, _ = console.request(
        "POST", "/api/router/test", body="{}", headers={"Host": "remote.test"}, cookie=False
    )
    assert status == 401


def test_access_log_labels_are_per_page_without_ids():
    assert server.route_label("GET", "/devices") == "spa/devices"
    assert server.route_label("GET", "/sandboxes") == "spa/sandboxes"
    assert server.route_label("GET", "/sandboxes/sbx-7f3a91c2") == "spa/sandboxes"
    assert server.route_label("GET", "/router") == "spa/router"
    assert server.route_label("GET", "/audit") == "spa/audit"


def test_old_html_form_post_is_gone(console):
    status, _, body = console.post("/router/test", {"q": "hello"})
    assert status == 404 and "not found" in body


# ---------------------------------------------------------------- sandboxes


def test_api_sandboxes_up_lists_the_fixture_row(console):
    status, data = j(console, "/api/sandboxes")
    assert status == 200 and data["configured"] is True and data["up"] is True
    assert any(s["sandboxID"] == "sbx-7f3a91c2" and s["templateID"] == "code-interpreter" for s in data["sandboxes"])
    assert "fixture" not in json.dumps(data)  # the control-plane key never reaches the client


def test_api_sandboxes_down_is_distinguishable_from_empty(console, monkeypatch):
    monkeypatch.setenv("CUBE_API_URL", "http://127.0.0.1:9")
    _, data = j(console, "/api/sandboxes")
    assert data["configured"] is True and data["up"] is False and data["sandboxes"] == []


def test_api_sandboxes_unconfigured(console, monkeypatch):
    monkeypatch.delenv("CUBE_API_URL")
    _, data = j(console, "/api/sandboxes")
    assert data["configured"] is False and data["up"] is False and data["api_host"] == ""


# ------------------------------------------------------------------- router


def test_api_router_shape_newest_first_and_capped(console):
    ledger = server.ledger_path()
    rows = [{"ts": 1000 + i, "event": "route", "route": f"r{i}", "engine": "table", "cost": 0} for i in range(20)]
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows))
    _, data = j(console, "/api/router")
    assert len(data["ledger"]) == consoleapi.ROUTER_LEDGER_LIMIT == 14
    assert data["ledger"][0]["route"] == "r19" and data["ledger"][-1]["route"] == "r6"
    assert {"paid", "dest_host", "per_route_cost", "spend", "cap"} <= set(data)


def test_router_test_validation_and_security_gates(console, calls):
    # no CSRF token -> refused before any routing
    status, _, _ = console.post("/api/router/test", {"q": "hi"}, csrf=False)
    assert status == 403 and calls["route"] == 0
    # cross-origin -> refused
    status, _, _ = console.post(
        "/api/router/test", {"q": "hi"}, headers={"Origin": "http://evil.example"}
    )
    assert status == 403 and calls["route"] == 0
    # empty utterance -> 400, nothing routed
    status, _, body = console.post("/api/router/test", {"q": "   "})
    assert status == 400 and "error" in json.loads(body) and calls["route"] == 0
    # utterance truncated to 500 chars before it is routed
    seen = []
    orig = server._route
    server._route = lambda q: seen.append(q) or orig(q)
    try:
        console.post("/api/router/test", {"q": "x" * 900})
    finally:
        server._route = orig
    assert len(seen[0]) == 500


def test_router_test_failure_says_nothing_was_sent(console, monkeypatch):
    def boom(q):
        raise RuntimeError("budget halted")

    monkeypatch.setattr(server, "_route", boom)
    status, _, body = console.post("/api/router/test", {"q": "hello"})
    assert status == 502
    assert json.loads(body)["error"] == "Routing failed: budget halted. Nothing was sent."


# -------------------------------------------------------------------- audit


def test_api_audit_filters_search_and_order(console):
    _, everything = j(console, "/api/audit")
    assert everything["total"] == everything["shown"] > 0 and everything["f"] == "all"
    ts = [r["ts"] for r in everything["rows"]]
    assert ts == sorted(ts, reverse=True)  # newest first
    for f, want in (("blocked", "blocked"), ("allowed", "allowed"), ("decisions", None)):
        _, data = j(console, f"/api/audit?f={f}")
        for r in data["rows"]:
            assert (r["result"] == want) if want else r["event"] == "human_decision"
    _, q = j(console, "/api/audit?q=GEMINI")  # case-insensitive across the row
    assert q["total"] >= 1 and all("gemini" in json.dumps(r).lower() for r in q["rows"])


def test_api_audit_validates_its_inputs(console):
    _, data = j(console, "/api/audit?f=bogus&q=" + "a" * 500)
    assert data["f"] == "all" and len(data["q"]) == 200


def test_audit_payload_caps_and_counts_matches():
    rows = [{"ts": i, "event": "cloud_call_allowed", "model": "m"} for i in range(250)]
    out = consoleapi.audit_payload(rows, "allowed", "")
    assert out["total"] == 250 and out["shown"] == consoleapi.AUDIT_LIMIT == 200
    assert out["rows"][0]["ts"] == 249 and out["rows"][0]["detail"] == "model=m"


def test_audit_payload_returns_raw_values_escaping_is_the_clients_job(console):
    audit = server.audit_path()
    with audit.open("a") as fh:
        fh.write(json.dumps({"ts": 9e9, "event": "note", "what": "<script>alert(1)</script>"}) + "\n")
    _, data = j(console, "/api/audit")
    assert data["rows"][0]["detail"] == "what=<script>alert(1)</script>"


def test_router_tester_is_a_post_only_api_route():
    assert ("/api/router/test", "p_api_router_test") in server.POST_ROUTES
    assert "/api/router/test" not in {p for p, _ in server.GET_ROUTES}


def test_sandbox_list_drops_non_object_entries(console, monkeypatch):
    """A null/str entry from the control plane must not reach the SPA, whose rows read fields off each entry."""
    monkeypatch.setattr(server, "_get", lambda url, key, timeout=3.0: (200, '[null, "x", 7, {"sandboxID": "sbx-1"}]'))
    _, data = j(console, "/api/sandboxes")
    assert data["sandboxes"] == [{"sandboxID": "sbx-1"}]
