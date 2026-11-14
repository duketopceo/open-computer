"""/api/skills reports the catalog on disk, and the live-health probe keeps a working address.

The move this fixes: `/api/skills` used to answer from `capabilities.collect()`, so the page named for skills was a
health dashboard. The rows are real information and stay reachable at `/api/health`; `/api/skills` now reads the
loader, so adding a SKILL.md adds a row with no code change.
"""

import json

from services.skills import CATALOG


def test_skills_reports_the_catalog_on_disk(console):
    code, _, body = console.get("/api/skills")
    assert code == 200
    rows = json.loads(body)["skills"]
    names = {r["name"] for r in rows}
    on_disk = {p.parent.name for p in CATALOG.glob("*/SKILL.md")}
    assert names == on_disk, "the endpoint reports exactly what is on disk"
    for r in rows:
        assert r["description"]
        assert isinstance(r["pulls_tools"], list)
        assert r["path"].endswith("SKILL.md")


def test_a_new_skill_file_adds_a_row_with_no_code_change(console, tmp_path, monkeypatch):
    catalog = tmp_path / "catalog"
    (catalog / "brand-new").mkdir(parents=True)
    (catalog / "brand-new" / "SKILL.md").write_text(
        "---\nname: brand-new\ndescription: Added by a test.\npulls_tools: sandbox\n---\n\nBody.\n"
    )
    monkeypatch.setattr("services.skills.CATALOG", catalog, raising=False)
    code, _, body = console.get("/api/skills")
    assert code == 200
    rows = json.loads(body)["skills"]
    assert [r["name"] for r in rows] == ["brand-new"]
    assert rows[0]["pulls_tools"] == ["sandbox"]


def test_a_skill_whose_capability_is_missing_reports_degraded(console, tmp_path, monkeypatch):
    catalog = tmp_path / "catalog"
    (catalog / "wants-memory").mkdir(parents=True)
    (catalog / "wants-memory" / "SKILL.md").write_text(
        "---\nname: wants-memory\ndescription: Needs recall.\npulls_tools: memory\n---\n\nBody.\n"
    )
    monkeypatch.setattr("services.skills.CATALOG", catalog, raising=False)
    monkeypatch.delenv("OC_MEMORY", raising=False)
    code, _, body = console.get("/api/skills")
    (row,) = json.loads(body)["skills"]
    assert row["status"] == "degraded"
    assert row["missing"] == ["memory"]
    assert row["description"] == "Needs recall.", "the description is not overwritten by the status word"


def test_an_unavailable_skill_does_not_break_the_endpoint(console, tmp_path, monkeypatch):
    catalog = tmp_path / "catalog"
    (catalog / "broken").mkdir(parents=True)
    (catalog / "broken" / "SKILL.md").write_text(
        "---\nname: broken\ndescription: d\npulls_tools: teleport\n---\n\nBody.\n"
    )
    monkeypatch.setattr("services.skills.CATALOG", catalog, raising=False)
    code, _, body = console.get("/api/skills")
    assert code == 200
    payload = json.loads(body)
    assert payload["skills"] == []
    assert "teleport" in payload["error"]


def test_an_absent_catalog_renders_an_empty_state_not_an_error(console, tmp_path, monkeypatch):
    monkeypatch.setattr("services.skills.CATALOG", tmp_path / "absent", raising=False)
    code, _, body = console.get("/api/skills")
    assert code == 200
    assert json.loads(body)["skills"] == []


def test_health_serves_the_live_probe_rows(console):
    code, _, body = console.get("/api/health")
    assert code == 200
    rows = json.loads(body)["skills"]
    hay = json.dumps(rows).lower()
    for word in ("OpenRouter", "llama", "Ollama", "Sandbox", "gate"):
        assert word.lower() in hay, word
    for st in ("ready", "down", "not configured", "active"):
        assert any(r.get("status") == st for r in rows) or st in hay, st


def test_the_two_endpoints_no_longer_answer_the_same_thing(console):
    _, _, a = console.get("/api/skills")
    _, _, b = console.get("/api/health")
    assert json.loads(a)["skills"] != json.loads(b)["skills"]


def test_the_unrouted_skills_page_is_gone():
    from services.opsview import server, views

    assert not hasattr(server.Handler, "g_skills")
    assert not hasattr(views, "page_skills")


def test_the_skills_path_still_serves_the_spa(console):
    code, _, _ = console.get("/skills")
    assert code == 200


def test_both_skills_routes_are_registered():
    from services.opsview import server

    names = {name for _, name in server.GET_ROUTES}
    assert "g_api_skills" in names
    assert "g_api_health" in names
    assert "g_skills" not in names
