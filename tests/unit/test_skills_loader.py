"""Skill loader (plan U1): a directory with a SKILL.md either loads or is refused by name.

The vocabulary is declared in `services/skills/inventory.py` and mapped to an enforcement site per token, so a
token is refused when the platform has nothing behind it. Nothing here dispatches a run; the resolver in
`tests/unit/test_skills_resolve.py` is what makes the declaration load-bearing.
"""

import pytest

from pathlib import Path

from services.skills import SkillError, load_all
from services.skills import inventory, schema


def write(root, name, text):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(text)
    return d


GOOD = """---
name: {name}
description: {description}
pulls_tools: {tools}
---

# {name}

Body text.
"""


def good(root, name, tools="memory, sandbox", description="Does a thing."):
    return write(root, name, GOOD.format(name=name, description=description, tools=tools))


def test_a_skill_with_two_declared_tools_loads(tmp_path):
    good(tmp_path, "notes")
    skills = load_all(tmp_path)
    assert len(skills) == 1
    s = skills[0]
    assert s.name == "notes"
    assert s.description == "Does a thing."
    assert s.pulls_tools == ("memory", "sandbox")
    assert s.path.name == "SKILL.md"


def test_an_out_of_vocabulary_token_is_refused_by_name(tmp_path):
    good(tmp_path, "notes", tools="memory, teleport")
    with pytest.raises(SkillError) as exc:
        load_all(tmp_path)
    msg = str(exc.value)
    assert "teleport" in msg
    assert "SKILL.md" in msg


def test_a_missing_description_is_refused_by_key(tmp_path):
    write(tmp_path, "notes", "---\nname: notes\npulls_tools: memory\n---\n\nBody.\n")
    with pytest.raises(SkillError) as exc:
        load_all(tmp_path)
    assert "description" in str(exc.value)


def test_a_name_that_disagrees_with_its_directory_is_refused(tmp_path):
    write(
        tmp_path,
        "notes",
        "---\nname: other\ndescription: Does a thing.\n---\n\nBody.\n",
    )
    with pytest.raises(SkillError) as exc:
        load_all(tmp_path)
    msg = str(exc.value)
    assert "other" in msg and "notes" in msg


def test_absent_pulls_tools_is_valid_and_requires_nothing(tmp_path):
    write(tmp_path, "notes", "---\nname: notes\ndescription: Does a thing.\n---\n\nBody.\n")
    (s,) = load_all(tmp_path)
    assert s.pulls_tools == ()


def test_a_bracketed_value_is_refused_as_one_unsplit_token(tmp_path):
    good(tmp_path, "notes", tools="[memory, sandbox]")
    with pytest.raises(SkillError) as exc:
        load_all(tmp_path)
    assert "[memory" in str(exc.value)


def test_crlf_frontmatter_parses_identically_to_lf(tmp_path):
    lf = tmp_path / "lf"
    crlf = tmp_path / "crlf"
    lf.mkdir()
    crlf.mkdir()
    write(lf, "notes", GOOD.format(name="notes", description="Does a thing.", tools="memory"))
    (crlf / "notes").mkdir()
    (crlf / "notes" / "SKILL.md").write_bytes(
        GOOD.format(name="notes", description="Does a thing.", tools="memory")
        .replace("\n", "\r\n")
        .encode()
    )
    a, b = load_all(lf)[0], load_all(crlf)[0]
    assert (a.name, a.description, a.pulls_tools, a.body) == (b.name, b.description, b.pulls_tools, b.body)


def test_a_missing_root_is_an_empty_set(tmp_path):
    assert load_all(tmp_path / "absent") == []


def test_a_body_over_the_cap_is_refused_naming_the_limit(tmp_path):
    d = tmp_path / "notes"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: notes\ndescription: Does a thing.\n---\n\n" + "x" * (inventory.MAX_BODY + 1)
    )
    with pytest.raises(SkillError) as exc:
        load_all(tmp_path)
    assert str(inventory.MAX_BODY) in str(exc.value)


def test_every_bad_skill_is_reported_in_one_raise(tmp_path):
    good(tmp_path, "alpha")
    good(tmp_path, "beta", tools="teleport")
    good(tmp_path, "gamma", tools="warp")
    with pytest.raises(SkillError) as exc:
        load_all(tmp_path)
    msg = str(exc.value)
    assert "beta" in msg and "gamma" in msg
    assert "teleport" in msg and "warp" in msg
    assert "alpha" not in msg


def test_the_vocabulary_maps_every_token_to_an_enforcement_site():
    for token, where in inventory.CAPABILITIES.items():
        assert isinstance(token, str) and token
        assert isinstance(where, str) and where, token


def test_an_mcp_token_is_checked_against_the_mcp_verbs(tmp_path):
    good(tmp_path, "notes", tools="mcp:list_runs")
    (s,) = load_all(tmp_path)
    assert s.pulls_tools == ("mcp:list_runs",)

    good(tmp_path, "other", tools="mcp:teleport")
    with pytest.raises(SkillError) as exc:
        load_all(tmp_path)
    assert "mcp:teleport" in str(exc.value)


def test_frontmatter_parses_into_a_flat_string_map():
    meta = schema.parse_frontmatter("---\nname: a\ndescription: b\n---\n\nBody.\n")
    assert meta == {"name": "a", "description": "b"}


def test_a_file_without_frontmatter_is_a_named_problem(tmp_path):
    write(tmp_path, "notes", "# No frontmatter here.\n")
    with pytest.raises(SkillError) as exc:
        load_all(tmp_path)
    assert "frontmatter" in str(exc.value).lower()


def test_the_shipped_catalog_loads_and_lives_inside_the_image_copy():
    from services.skills import CATALOG

    skills = load_all(CATALOG)
    assert skills, "the shipped catalog is empty"
    # deploy/server-001/Dockerfile copies `services`, so a catalog under it reaches the runtime image.
    assert CATALOG.is_relative_to(Path(__file__).resolve().parents[2] / "services")
    for s in skills:
        assert s.name == s.path.parent.name
    # At least one seed declares a capability, so the page is not decorative and the vocabulary is exercised.
    # Not every seed has to: a skill needing nothing (local paths on a device origin) is a valid skill.
    assert any(s.pulls_tools for s in skills), "no shipped seed declares a capability"
