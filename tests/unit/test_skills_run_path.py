"""The run path resolves skills before its first dispatch, and records what it resolved.

Model calls go to the in-process fake OpenRouter; nothing reaches a real provider. The catalog is injected via the
`OC_SKILLS_CATALOG`-free path: `resolve_run` reads the shipped catalog, so these tests drive the shipped one and
add a temporary catalog only where a refusal needs a bad declaration.
"""

import json

import pytest

from services.orchestrator import Orchestrator
from services.orchestrator.skills import SkillsLog

from fake_openrouter import FakeOpenRouter


@pytest.fixture
def fake(monkeypatch):
    with FakeOpenRouter() as f:
        monkeypatch.setenv("OC_OPENROUTER_BASE_URL", f.url)
        yield f


def _cloud():
    from services.placement import Placement

    return Placement("cloud-OpenRouter", "test")


def _plan(*subs):
    return lambda prompt: json.dumps(list(subs) or [{"id": "a", "title": "only", "prompt": "p"}])


def test_a_run_records_what_it_resolved_before_its_first_dispatch(fake, tmp_path):
    orch = Orchestrator(tmp_path, place_fn=lambda *a, **k: _cloud())
    rid = orch.submit("goal", _plan())
    row = SkillsLog(tmp_path / "skills.ndjson").for_run(rid)
    assert row is not None
    assert row["skills"], "the shipped catalog is non-empty, so a run resolves at least one skill"


def test_the_record_precedes_the_first_dispatch_row(fake, tmp_path):
    orch = Orchestrator(tmp_path, place_fn=lambda *a, **k: _cloud())
    orch.submit("goal", _plan())
    skills_ts = SkillsLog(tmp_path / "skills.ndjson").rows()[0]["ts"]
    dispatch_rows = [r for r in orch.audit.rows() if r.get("event") == "dispatch"]
    assert dispatch_rows, "the run dispatched at least one step"
    assert skills_ts <= dispatch_rows[0]["ts"]


def test_a_declaration_this_build_cannot_honour_refuses_the_run(fake, tmp_path, monkeypatch):
    catalog = tmp_path / "catalog"
    (catalog / "impossible").mkdir(parents=True)
    (catalog / "impossible" / "SKILL.md").write_text(
        "---\nname: impossible\ndescription: d\npulls_tools: memory\n---\n\nBody.\n"
    )
    monkeypatch.setattr("services.skills.resolve.CATALOG", catalog, raising=False)
    monkeypatch.delenv("OC_MEMORY", raising=False)
    orch = Orchestrator(tmp_path, place_fn=lambda *a, **k: _cloud())
    rid = orch.submit("goal", _plan())
    failed = [r for r in orch.audit.rows() if r.get("event") == "run_failed"]
    assert failed, "the run was refused"
    assert failed[0]["run"] == rid
    assert failed[0]["stage"] == "skills"
    assert "memory" in failed[0]["error"]
    assert [r for r in orch.audit.rows() if r.get("event") == "dispatch"] == []


def test_the_refusal_reason_names_the_skill_and_the_token(fake, tmp_path, monkeypatch):
    catalog = tmp_path / "catalog"
    (catalog / "needs-memory").mkdir(parents=True)
    (catalog / "needs-memory" / "SKILL.md").write_text(
        "---\nname: needs-memory\ndescription: d\npulls_tools: memory\n---\n\nBody.\n"
    )
    monkeypatch.setattr("services.skills.resolve.CATALOG", catalog, raising=False)
    monkeypatch.delenv("OC_MEMORY", raising=False)
    orch = Orchestrator(tmp_path, place_fn=lambda *a, **k: _cloud())
    orch.submit("goal", _plan())
    (failed,) = [r for r in orch.audit.rows() if r.get("event") == "run_failed"]
    assert "needs-memory" in failed["error"] and "memory" in failed["error"]


def test_a_catalog_that_fails_to_load_refuses_rather_than_raising(fake, tmp_path, monkeypatch):
    catalog = tmp_path / "catalog"
    (catalog / "bad").mkdir(parents=True)
    (catalog / "bad" / "SKILL.md").write_text("---\nname: bad\ndescription: d\npulls_tools: teleport\n---\n\nB.\n")
    monkeypatch.setattr("services.skills.resolve.CATALOG", catalog, raising=False)
    orch = Orchestrator(tmp_path, place_fn=lambda *a, **k: _cloud())
    rid = orch.submit("goal", _plan())  # must not raise
    (failed,) = [r for r in orch.audit.rows() if r.get("event") == "run_failed"]
    assert failed["run"] == rid and "teleport" in failed["error"]


def test_an_empty_catalog_dispatches_normally(fake, tmp_path, monkeypatch):
    monkeypatch.setattr("services.skills.resolve.CATALOG", tmp_path / "absent", raising=False)
    orch = Orchestrator(tmp_path, place_fn=lambda *a, **k: _cloud())
    rid = orch.submit("goal", _plan())
    assert not [r for r in orch.audit.rows() if r.get("event") == "run_failed"]
    assert SkillsLog(tmp_path / "skills.ndjson").for_run(rid)["skills"] == []


def test_an_unwritable_skills_log_does_not_change_the_outcome(fake, tmp_path, monkeypatch):
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    monkeypatch.setattr("services.skills.resolve.CATALOG", tmp_path / "absent", raising=False)
    orch = Orchestrator(tmp_path, place_fn=lambda *a, **k: _cloud())
    orch.skills = SkillsLog(blocked)  # a directory: every append fails
    rid = orch.submit("goal", _plan())
    assert not [r for r in orch.audit.rows() if r.get("event") == "run_failed"]
    assert any(r.get("run") == rid for r in orch.audit.rows())


def test_garbage_lines_in_the_skills_log_are_skipped(tmp_path):
    p = tmp_path / "skills.ndjson"
    p.write_text('{"run":"r","skills":[]}\nnot json\n[1]\n')
    assert [r["run"] for r in SkillsLog(p).rows()] == ["r"]
