"""Frontmatter parsing and per-entry validation for skills.

`parse_frontmatter` is deliberately dull: the leading `---` block is a flat `str -> str` map, so a skill cannot
carry nested structure that no reader understands. `problems` never raises — it returns every reason one entry is
unusable, and `services.skills.load_all` turns a non-empty list into one `SkillError` for the whole catalog.
"""

from __future__ import annotations

from pathlib import Path

from . import inventory


def normalize(text: str) -> str:
    """Fold CRLF and lone CR to LF, so a skill edited on Windows hashes out the same as one edited here."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_frontmatter(text: str) -> dict[str, str]:
    """The leading `---` block as a flat map. Empty when the file has no frontmatter."""
    lines = normalize(text).split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    meta: dict[str, str] = {}
    for ln in lines[1:]:
        if ln.strip() == "---":
            return meta
        key, sep, value = ln.partition(":")
        if not sep:
            continue
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return {}  # an unterminated block is no frontmatter


def body_of(text: str) -> str:
    """Everything after the closing `---` of the frontmatter, normalized to LF."""
    lines = normalize(text).split("\n")
    if not lines or lines[0].strip() != "---":
        return normalize(text)
    for i, ln in enumerate(lines[1:], start=1):
        if ln.strip() == "---":
            return "\n".join(lines[i + 1 :])
    return normalize(text)


def split_tools(raw: str) -> tuple[str, ...]:
    """`pulls_tools: a, b` -> `("a", "b")`. Empty means the skill requires nothing."""
    return tuple(t.strip() for t in raw.split(",") if t.strip())


def problems(entry: dict[str, str], path: Path, body: str, verbs: tuple[str, ...] = ()) -> list[str]:
    """Every reason this entry cannot load, naming the file, the field and the offending value. Never raises."""
    out: list[str] = []
    where = str(path)
    directory = path.parent.name
    name = entry.get("name", "")
    if not name:
        out.append(f"{where}: frontmatter has no `name`")
    elif name != directory:
        out.append(f"{where}: `name` is {name!r}, which is not its directory name {directory!r}")
    if not entry.get("description", "").strip():
        out.append(f"{where}: frontmatter has no `description`")
    for token in split_tools(entry.get("pulls_tools", "")):
        if not inventory.known(token, verbs):
            site = inventory.PLACEMENT_FLAGS.get(token) or inventory.CAPABILITIES.get(token) or ""
            hint = f" (enforced by {site})" if site else ""
            out.append(f"{where}: `pulls_tools` names {token!r}, which is not a capability{hint}")
    if len(body) > inventory.MAX_BODY:
        out.append(f"{where}: body is {len(body)} bytes, over the {inventory.MAX_BODY}-byte limit")
    return out
