"""/skills + /automations (F4): capability registry renders live health;
the schedules surface is honest when Temporal is away — never guesses."""

import json

from services.opsview import capabilities


def test_skills_page_lists_real_capabilities(console):
    code, _, body = console.get("/api/skills")
    skills = json.loads(body)["skills"]
    hay = json.dumps(skills).lower()
    for word in ("OpenRouter", "llama", "Ollama", "Sandbox", "gate"):
        assert word.lower() in hay, word
    for st in ("ready", "down", "not configured", "active"):
        assert any(s.get("status") == st for s in skills) or st in hay, st


def test_capabilities_report_live_not_static():
    rows = capabilities.collect()
    assert len(rows) >= 6
    for r in rows:
        assert r["name"] and r["kind"] and r["status"] and r["detail"]
        assert r["status"] in ("ready", "down", "not configured", "active")


def test_automations_page_shows_outage_when_temporal_away(console, monkeypatch):
    # OC_DURABLE unset in the fixture env → disabled → honest outage, not a fake list
    monkeypatch.delenv("OC_DURABLE", raising=False)
    code, _, body = console.get("/api/automations")
    assert code == 200
    payload = json.loads(body)
    assert payload["enabled"] is False and payload["automations"] == []


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
