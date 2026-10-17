"""One registry feeds the sidebar, the palette and the `?` overlay. A test
enforces that the palette covers 100% of the actions on screen (section 7)."""

import json
import re

from services.opsview import registry, server
from services.workspace import routes as workspace_routes


def embedded(html):
    return json.loads(
        re.search(r'<script type="application/json" id="oc-actions">(.*?)</script>', html, re.S)
        .group(1)
        .replace("<\\/", "</")
    )


def test_no_key_collisions():
    assert registry.key_conflicts() == []


def test_keys_match_tallyline():
    k = {a.id: a.keys for a in registry.ACTIONS}
    assert k["palette"] == "ctrl+k" and k["help"] == "?" and k["close"] == "esc"
    assert k["row.down"] == "j / ↓" and k["row.up"] == "k / ↑" and k["row.open"] == "enter"
    assert k["nav.approvals"] == "g r"  # Tallyline: g r review queue
    assert k["nav.audit"] == "g e"  # Tallyline: g e activity
    assert k["approval.approve"] == "a" and k["approval.sendback"] == "r" and k["run.new"] == "c"


def test_sidebar_sections_in_order():
    assert [a.label for a in registry.nav_actions()] == [
        "Work",
        "Overview",
        "Goals",
        "Automations",
        "Skills",
        "Sandboxes",
        "Runs",
        "Approvals",
        "Router",
        "Audit",
    ]
    assert tuple(a.id.split(".")[1] for a in registry.nav_actions()) == registry.PAGES


def test_every_nav_href_is_a_get_route():
    import re as _re

    for a in registry.ACTIONS:
        if a.href:
            path = a.href.split("?")[0]
            assert any(_re.fullmatch(p, path) for p, _ in [*server.GET_ROUTES, *workspace_routes.GET_ROUTES]), a.id


def test_every_page_embeds_exactly_its_registry_slice(console):
    for page, path in {
        "work": "/work",
        "overview": "/",
        "goals": "/goals",
        "automations": "/automations",
        "skills": "/skills",
        "sandboxes": "/sandboxes",
        "runs": "/runs",
        "approvals": "/approvals",
        "router": "/router",
        "audit": "/audit",
    }.items():
        _, _, html = console.get(path)
        ids = [a["id"] for a in embedded(html)]
        assert ids == [a.id for a in registry.for_page(page)], page


def test_every_on_screen_action_is_in_the_palette_payload(console):
    for path in (
        "/",
        "/work",
        "/goals",
        "/automations",
        "/skills",
        "/sandboxes",
        "/runs",
        "/runs/new",
        "/approvals",
        "/router",
        "/audit",
    ):
        _, _, html = console.get(path)
        palette_ids = {a["id"] for a in embedded(html)}
        on_screen = set(re.findall(r'data-action="([a-z.]+)"', html))
        assert on_screen <= palette_ids, (path, on_screen - palette_ids)
        assert on_screen <= set(registry.BY_ID)


def test_overlay_and_palette_are_generated_not_hardcoded():
    js = (server.STATIC / "console.js").read_text()
    assert "oc-actions" in js
    # no action label duplicated in the script (dialog aria-labels excepted)
    for a in registry.ACTIONS:
        if a.label in ("Command palette", "Keyboard shortcuts"):
            continue
        assert f'"{a.label}"' not in js, a.label
    assert "var order = []; ACTIONS.forEach" in js  # help groups come from the registry too


def test_help_overlay_lists_every_keyed_action():
    keyed = [a for a in registry.ACTIONS if a.keys]
    assert len(keyed) >= 15
    assert all(
        a.group
        in ("Global", "Navigate", "Lists", "Goals", "Work", "Approvals", "Router", "Audit", "Workspace", "Sidebar")
        for a in keyed
    )
