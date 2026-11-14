"""Skills: a directory per skill, a `SKILL.md` that declares itself, and a loader that refuses an invalid one.

The shape follows `services/router/model_catalog.py`: load what is on disk, validate everything, and raise one
error that names each bad value rather than guessing. `CatalogError(ValueError)` there is the precedent this
mirrors; the difference is that a catalog lives in one JSON file and skills are a tree.

The catalog lives at `services/skills/catalog/` rather than a repo-root `skills/`, because the runtime image
copies only `oc`, `services`, `pyproject.toml`, `README.md` and `ui/dist` (deploy/server-001/Dockerfile). A tree
outside those paths would never ship, and a skill that does not ship is not a skill.

Nothing here loads skills for a run. `services/skills/resolve.py` is what holds a run to a declaration, and it is
the only reason validation is load-bearing rather than cosmetic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import inventory, schema

CATALOG = Path(__file__).with_name("catalog")


class SkillError(ValueError):
    """One or more skills cannot load. The message names every file and every bad value."""


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    pulls_tools: tuple[str, ...]
    path: Path
    body: str
    applies_to: tuple[str, ...] = ()

    def public(self) -> dict:
        """What the console shows. The body is not sent: it can be 64 KB and the page lists skills."""
        return {
            "name": self.name,
            "description": self.description,
            "pulls_tools": list(self.pulls_tools),
            "applies_to": list(self.applies_to),
            "path": str(self.path),
        }


def load_all(root: Path | str | None = None) -> list[Skill]:
    """Every skill under `root`, or one `SkillError` naming all the problems. A missing root is an empty list.

    `root` defaults to the shipped catalog and is resolved at call time, so a caller (a test, a per-deployment
    tree) can point loading at a different directory without the default having been frozen at import.
    """
    root = Path(CATALOG if root is None else root)
    if not root.is_dir():
        return []
    verbs = inventory.mcp_verbs()
    found: list[Skill] = []
    problems: list[str] = []
    for entry in sorted(p for p in root.iterdir() if p.is_dir()):
        md = entry / "SKILL.md"
        if not md.is_file():
            continue
        try:
            if md.stat().st_size > inventory.MAX_BODY + 4096:
                problems.append(f"{md}: over the {inventory.MAX_BODY}-byte body limit")
                continue
            raw = md.read_bytes()
        except OSError as e:
            problems.append(f"{md}: could not be read ({e})")
            continue
        text = raw.decode("utf-8", "replace")
        meta = schema.parse_frontmatter(text)
        if not meta:
            problems.append(f"{md}: no frontmatter block")
            continue
        body = schema.body_of(text)
        problems.extend(schema.problems(meta, md, body, verbs))
        found.append(
            Skill(
                name=meta.get("name", ""),
                description=meta.get("description", ""),
                pulls_tools=schema.split_tools(meta.get("pulls_tools", "")),
                path=md,
                body=body,
                applies_to=schema.split_tools(meta.get("applies_to", "")),
            )
        )
    if problems:
        raise SkillError("; ".join(problems))
    return found


__all__ = ["CATALOG", "Skill", "SkillError", "inventory", "load_all", "schema"]
