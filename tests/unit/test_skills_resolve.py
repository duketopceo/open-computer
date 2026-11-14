"""Run-side skill resolution (plan U2): a run is held to what its skills declared.

The distinction these tests fix: a declaration this build cannot honour stops the run, while a capability the
plan simply does not use is not a refusal. Gating on usage would refuse ordinary runs for not needing what they
declared, which inverts the point of declaring it.
"""

from pathlib import Path

from services.skills import load_all
from services.skills import resolve as R

GOOD = "---\nname: {name}\ndescription: d\npulls_tools: {tools}\n{extra}---\n\nBody.\n"


def catalog(root, **skills):
    for name, spec in skills.items():
        tools = spec.get("tools", "")
        extra = f"applies_to: {spec['applies_to']}\n" if "applies_to" in spec else ""
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(GOOD.format(name=name, tools=tools, extra=extra))


def test_a_declaration_the_build_cannot_honour_stops_the_run(tmp_path):
    catalog(tmp_path, notes={"tools": "memory"})
    r = R.resolve_run(tmp_path, caps=frozenset({"sandbox"}))
    assert not r.ok
    assert r.missing == (("notes", "memory"),)
    assert "notes" in r.reason() and "memory" in r.reason()


def test_the_same_skill_proceeds_when_the_build_provides_it(tmp_path):
    catalog(tmp_path, notes={"tools": "memory"})
    r = R.resolve_run(tmp_path, caps=frozenset({"memory"}))
    assert r.ok
    assert [s.name for s in r.skills] == ["notes"]


def test_a_run_is_not_refused_for_a_capability_its_plan_does_not_use(tmp_path):
    # The point of the distinction: a web skill does not stop a run with no web step.
    catalog(tmp_path, web={"tools": "web"})
    subtasks = [{"id": "s1", "needs_sandbox": True, "needs_web": False}]
    r = R.resolve_run(tmp_path, subtasks=subtasks, caps=frozenset({"web"}))
    assert r.ok
    assert r.usage == ("sandbox",)


def test_usage_records_the_flags_the_plan_actually_carries(tmp_path):
    catalog(tmp_path, web={"tools": "web"})
    subtasks = [{"id": "s1", "needs_web": True}, {"id": "s2", "needs_egress": True}]
    r = R.resolve_run(tmp_path, subtasks=subtasks, caps=frozenset({"web", "egress"}))
    assert r.usage == ("egress", "web")


def test_an_empty_catalog_resolves_to_an_empty_set_and_is_not_an_error(tmp_path):
    r = R.resolve_run(tmp_path / "absent")
    assert r.ok
    assert r.skills == ()


def test_a_catalog_that_fails_to_load_is_a_reason_not_a_traceback(tmp_path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: broken\ndescription: d\npulls_tools: teleport\n---\n\nBody.\n")
    r = R.resolve_run(tmp_path)
    assert not r.ok
    assert "teleport" in r.reason()


def test_applies_to_narrows_a_skill_to_the_origins_it_names(tmp_path):
    catalog(tmp_path, notes={"tools": "sandbox", "applies_to": "device"})
    assert R.resolve_run(tmp_path, origin="cloud", caps=frozenset({"sandbox"})).skills == ()
    assert len(R.resolve_run(tmp_path, origin="device", caps=frozenset({"sandbox"})).skills) == 1


def test_a_skill_without_applies_to_applies_to_every_run(tmp_path):
    catalog(tmp_path, notes={"tools": "sandbox"})
    for origin in ("cloud", "device", "loopback"):
        assert len(R.resolve_run(tmp_path, origin=origin, caps=frozenset({"sandbox"})).skills) == 1


def test_available_reports_what_this_build_provides():
    caps = R.available(memory_enabled=False, verbs=("list_runs",))
    assert {"sandbox", "egress"} <= caps
    assert "memory" not in caps
    assert "mcp:list_runs" in caps
    assert "mcp:get_run" not in caps


def test_memory_availability_follows_configuration():
    assert "memory" not in R.available(memory_enabled=False)
    assert "memory" in R.available(memory_enabled=True)


def test_the_resolved_record_is_json_shaped():
    from services.skills import Skill

    s = Skill(
        name="notes",
        description="d",
        pulls_tools=("sandbox",),
        path=Path("/x/SKILL.md"),
        body="",
    )
    r = R.resolve_for_run([s], subtasks=[{"id": "s1", "needs_sandbox": True}], caps=frozenset({"sandbox"}))
    pub = r.public()
    assert pub["skills"] == ["notes"]
    assert pub["tools"] == ["sandbox"]
    assert pub["usage"] == ["sandbox"]
    assert pub["missing"] == []


def test_the_shipped_catalog_resolves_against_the_real_build():
    r = R.resolve_run()
    assert r.ok, r.reason()
    assert len(r.skills) >= 2


def test_every_shipped_skill_token_is_in_the_vocabulary():
    from services.skills import CATALOG, inventory

    verbs = inventory.mcp_verbs()
    for s in load_all(CATALOG):
        for token in s.pulls_tools:
            assert inventory.known(token, verbs), f"{s.name}: {token}"
