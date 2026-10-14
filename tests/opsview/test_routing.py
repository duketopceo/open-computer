import re

import pytest

from services.opsview import registry, server

SAMPLE_ARGS = {"id": "x"}


def concrete(pattern: str) -> str:
    return (pattern.replace("(?P<id>[^/]+)", "sbx-7f3a91c2")
            .replace("(?P<id>run_[0-9a-f]{8})", "run_00000000")
            .replace("(?P<id>goal_[0-9a-f]{8})", "goal_00000000")
            .replace("(?P<id>ap_[0-9a-f]{8})", "ap_00000000")
            .replace("(?P<id>auto-[0-9a-f]{8})", "auto-00000000"))


@pytest.mark.parametrize("path", ["/", "/sandboxes", "/sandboxes?inline=1", "/runs", "/runs/new",
                                  "/approvals", "/router", "/audit", "/audit?f=blocked&q=gpt",
                                  "/_frag/fleet", "/_frag/status"])
def test_pages_render_200(console, path):
    status, headers, html = console.get(path)
    assert status == 200
    assert "Content-Security-Policy" in headers and "script-src 'self'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"


def test_every_sidebar_section_has_a_route(console):
    for a in registry.nav_actions():
        assert console.get(a.href)[0] == 200, a.href
    _, _, html = console.get("/")
    for label in ("Overview", "Goals", "Sandboxes", "Runs", "Approvals", "Router", "Audit"):
        assert f"<span>{label}</span>" in html


def test_unauthenticated_remote_requests_go_to_login(console):
    remote = {"Host": "remote.test"}
    for path in ("/", "/approvals", "/runs/new", "/_frag/fleet", "/sandboxes"):
        status, headers, _ = console.get(path, cookie=False, headers=remote)
        assert status == 302 and headers["Location"] == "/login"
    status, headers, _ = console.get("/", cookie=False)  # loopback is the trusted surface
    assert status == 200 and "oc_sess=" in headers.get("Set-Cookie", "")
    assert console.get("/static/console.css", cookie=False)[0] == 200  # tokens are public


def test_legacy_views_redirect(console):
    for old, new in {"/fleet": "/sandboxes", "/matrix": "/runs", "/gate": "/approvals", "/ops": "/"}.items():
        status, headers, _ = console.get(old)
        assert (status, headers["Location"]) == (302, new)


def test_unknown_path_404_and_static_whitelist(console):
    assert console.get("/nope")[0] == 404
    assert console.get("/static/../server.py")[0] == 404
    assert console.get("/static/auth.py")[0] == 404
    assert console.get("/runs/run_zzzzzzzz")[0] == 404
    assert console.get("/runs/run_00000000")[0] == 404
    assert console.get("/sandboxes/does-not-exist")[0] == 404


def test_sandbox_detail_and_inline_inventory(console):
    _, _, html = console.get("/sandboxes?inline=1")
    assert "sbx-7f3a91c2" in html and "code-interpreter" in html
    status, _, html = console.get("/sandboxes/sbx-7f3a91c2")
    assert status == 200 and "templateID" in html


def test_sandboxes_page_uses_skeleton_then_fragment(console):
    _, _, html = console.get("/sandboxes")
    assert 'data-frag="/_frag/fleet"' in html and "skeleton" in html and 'aria-busy="true"' in html
    assert "<svg" in html and "spinner" not in html.lower()


def test_route_tables_are_disjoint_and_named():
    for _, name in server.GET_ROUTES:
        assert name.startswith("g_") or name.startswith("g_frag"), name
        assert hasattr(server.Handler, name)
    for _, name in server.POST_ROUTES:
        assert name.startswith("p_") and hasattr(server.Handler, name)
    assert {p for p, _ in server.GET_ROUTES}.isdisjoint({p for p, _ in server.POST_ROUTES} - {"/runs", "/goals", "/automations"})


def test_api_down_is_honest(console, monkeypatch):
    monkeypatch.setenv("CUBE_API_URL", "http://127.0.0.1:9")
    _, _, html = console.get("/_frag/fleet")
    assert "Control plane is down" in html and "unknown, not empty" in html
    monkeypatch.delenv("CUBE_API_URL")
    _, _, html = console.get("/_frag/fleet")
    assert "not configured" in html.lower()
