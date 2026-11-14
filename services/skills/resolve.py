"""Run-side skill resolution. What this run resolved, and whether the platform can honour it.

Why this module exists at all: nothing under `services/agents`, `services/orchestrator` or `services/durable`
loads a skill, so a loader alone would guard a console page and nothing else. This is the consumer that makes
`pulls_tools` load-bearing — a skill declaring a capability this build cannot provide stops the run before it
dispatches, instead of discovering the gap mid-flight.

What "cannot provide" means, precisely. A token is available when its enforcement site exists in this build and
this configuration:

  sandbox  the execplane can make a sandbox at all
  egress   the gate exists, so an egress step has somewhere to be approved
  web      services/orchestrator/tools/web.py is importable
  apps     services/connectors/apps_tools.py is importable
  memory   OC_MEMORY=kurultai, so a MemoryClient exists
  mcp:<verb>  the verb is a key of the MCP TOOLS dict

Availability is a statement about the build, never about whether this particular plan happens to use the
capability. A run that has no web step is not refused for carrying a web skill; it simply will not reach that
path. Gating on usage instead would refuse ordinary runs for not needing what they declared, which inverts the
point of the declaration.

The run's own usage is still recorded, so the console can show what the plan was going to touch.

Applicability: a skill applies to every run unless its frontmatter names `applies_to`, in which case it applies
only to a run whose origin matches. There is no per-run skill selector today; a later one replaces this rule
rather than being guessed at.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

from . import CATALOG, Skill, SkillError, inventory, load_all

# Which subtask flag shows the plan will exercise a token. Absent means the token is not usage-tracked.
USE_FLAGS: dict[str, str] = dict(inventory.PLACEMENT_FLAGS)

_MCP = "mcp"


@dataclass(frozen=True)
class Resolved:
    """What one run resolved. `missing` is (skill name, token) for every declaration this build cannot honour."""

    skills: tuple[Skill, ...] = ()
    missing: tuple[tuple[str, str], ...] = ()
    usage: tuple[str, ...] = ()
    problems: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return not self.missing and not self.problems

    def reason(self) -> str:
        """The failure text a refused run carries. Names the skill and the token, never a stack trace."""
        parts = [f"skill {name!r} needs {token!r}, which this build cannot provide" for name, token in self.missing]
        parts.extend(self.problems)
        return "; ".join(parts)

    def public(self) -> dict:
        return {
            "skills": [s.name for s in self.skills],
            "tools": sorted({t for s in self.skills for t in s.pulls_tools}),
            "usage": list(self.usage),
            "missing": [{"skill": n, "token": t} for n, t in self.missing],
            "problems": list(self.problems),
        }


def _importable(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def available(memory_enabled: bool | None = None, verbs: tuple[str, ...] = ()) -> frozenset[str]:
    """The capability tokens this build can honour. `memory` and `mcp:<verb>` depend on configuration and registry."""
    from services.memory import MemoryConfig

    enabled = (MemoryConfig.from_env() is not None) if memory_enabled is None else memory_enabled
    out = {"sandbox", "egress"}
    if _importable("services.orchestrator.tools.web"):
        out.add("web")
    if _importable("services.connectors.apps_tools"):
        out.add("apps")
    if enabled:
        out.add("memory")
    out.update(f"{_MCP}:{v}" for v in (verbs or inventory.mcp_verbs()))
    return frozenset(out)


def applies(skill: Skill, origin: str) -> bool:
    """`applies_to` absent means every run; present means the origins it names, comma-separated."""
    targets = skill.applies_to
    return not targets or origin in targets


def usage_of(subtasks) -> tuple[str, ...]:
    """The tokens this plan's steps will actually exercise, from the flags each step carries."""
    if not subtasks:
        return ()
    used = {tok for tok, flag in USE_FLAGS.items() if any(s.get(flag) for s in subtasks)}
    if any(s.get("needs_apps") for s in subtasks):
        used.add("apps")
    return tuple(sorted(used))


def resolve_for_run(
    skills: list[Skill],
    *,
    origin: str = "cloud",
    subtasks=None,
    caps: frozenset[str] | None = None,
) -> Resolved:
    """Select the skills this run applies and check every declared token against what the build provides.

    Never raises: a catalog that fails to load becomes a `problems` entry, so the caller reports one run failure
    rather than an exception escaping the scheduler.
    """
    caps = available() if caps is None else caps
    selected = tuple(s for s in skills if applies(s, origin))
    missing = tuple(
        (s.name, token) for s in selected for token in s.pulls_tools if not inventory.known_token(token, caps)
    )
    return Resolved(skills=selected, missing=missing, usage=usage_of(subtasks))


def resolve_run(
    root: Path | str | None = None,
    *,
    origin: str = "cloud",
    subtasks=None,
    caps: frozenset[str] | None = None,
) -> Resolved:
    """`resolve_for_run` for a catalog on disk. A load failure is a `problems` entry, not an exception.

    `root` defaults to the shipped catalog and is resolved at call time, not import time, so a caller can point a
    run at a different catalog (tests, a per-deployment tree) without the default having been frozen.
    """
    try:
        skills = load_all(CATALOG if root is None else root)
    except SkillError as e:
        return Resolved(problems=(str(e),))
    return resolve_for_run(skills, origin=origin, subtasks=subtasks, caps=caps)


__all__ = ["Resolved", "applies", "available", "resolve_for_run", "resolve_run", "usage_of"]
