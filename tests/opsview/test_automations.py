"""/skills + /automations (F4): capability registry renders live health;
the schedules surface is honest when Temporal is away — never guesses."""

from services.opsview import capabilities


def test_skills_page_lists_real_capabilities(console):
    code, _, html = console.get("/skills")
    assert code == 200
    for word in ("Capability", "OpenRouter", "llama", "Ollama", "Sandbox", "gate", "cap"):
        assert word.lower() in html.lower(), word
    # live probe results render as normative state words
    for st in ("ready", "down", "not configured", "active"):
        assert f'>{st}<' in html or f'>{st.title()}<' in html, st


def test_capabilities_report_live_not_static():
    rows = capabilities.collect()
    assert len(rows) >= 6
    for r in rows:
        assert r["name"] and r["kind"] and r["status"] and r["detail"]
        assert r["status"] in ("ready", "down", "not configured", "active")


def test_automations_page_shows_outage_when_temporal_away(console, monkeypatch):
    # OC_DURABLE unset in the fixture env → disabled → honest outage, not a fake list
    monkeypatch.delenv("OC_DURABLE", raising=False)
    code, _, html = console.get("/automations")
    assert code == 200
    assert "unreachable" in html.lower() or "No automations" in html
    assert "New automation" in html  # the create form still renders


def test_automations_create_validates_input(console):
    code, _, html = console.post("/automations", {"goal": "", "every_minutes": "60"})
    assert code == 400
    code, _, html = console.post("/automations", {"goal": "x", "every_minutes": "0"})
    assert code == 400
    code, _, html = console.post("/automations", {"goal": "x", "every_minutes": "999999"})
    assert code == 400


def test_automation_posts_are_csrf_gated(console):
    code, _, _ = console.post("/automations", {"goal": "g", "every_minutes": "60"}, csrf=False)
    assert code == 403
    code, _, _ = console.post("/automations/auto-deadbeef/pause", {"paused": "1"}, csrf=False)
    assert code == 403
    code, _, _ = console.post("/automations/auto-deadbeef/delete", {"confirm": "1"}, csrf=False)
    assert code == 403
