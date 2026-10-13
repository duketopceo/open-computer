"""Single action registry. The sidebar, the Cmd/Ctrl+K palette and the `?`
overlay are all generated from ACTIONS; nothing else may define a shortcut.

Keys follow Tallyline (docs/design/silver-lining.md section 7): `g` then a
letter navigates, j/k/arrows move, Enter opens, Esc closes the top layer,
`?` lists shortcuts, `c` creates. Where an action exists in both products it
uses the same key (g r review queue = approvals, g e activity = audit).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

PAGES = ("work", "overview", "goals", "sandboxes", "runs", "approvals", "router", "audit")


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    group: str  # Navigate | Global | Lists | Approvals | Runs | Router | Audit
    keys: str = ""  # display form, e.g. "g o", "a", "ctrl+k"
    scope: str = "global"  # "global" or one page id
    href: str = ""  # navigation target (a GET route)
    note: str = ""  # cost / destination shown next to the label


ACTIONS: tuple[Action, ...] = (
    # Navigate: the sidebar sections, in order
    Action("nav.work", "Work", "Navigate", "g w", href="/work"),
    Action("nav.overview", "Overview", "Navigate", "g o", href="/"),
    Action("nav.goals", "Goals", "Navigate", "g t", href="/goals"),
    Action("nav.sandboxes", "Sandboxes", "Navigate", "g x", href="/sandboxes"),
    Action("nav.runs", "Runs", "Navigate", "g n", href="/runs"),
    Action("nav.approvals", "Approvals", "Navigate", "g r", href="/approvals"),
    Action("nav.router", "Router", "Navigate", "g u", href="/router"),
    Action("nav.audit", "Audit", "Navigate", "g e", href="/audit"),
    # Global
    Action("palette", "Command palette", "Global", "ctrl+k"),
    Action("help", "Keyboard shortcuts", "Global", "?"),
    Action("theme", "Toggle theme (dark, light, system)", "Global"),
    Action("run.new", "New matrix run", "Global", "c", href="/runs/new",
           note="Boots microVMs. No model spend."),
    Action("close", "Close layer / clear selection", "Global", "esc"),
    # Lists
    Action("row.down", "Move down", "Lists", "j / ↓"),
    Action("row.up", "Move up", "Lists", "k / ↑"),
    Action("row.open", "Open selected", "Lists", "enter"),
    # Goals
    Action("goal.new", "New goal", "Goals", "n", "goals", href="/goals/new",
           note="Decomposes into subtasks and runs them. Model spend."),
    # Work
    Action("work.compose", "Focus goal composer", "Work", "/", "work",
           note="Describe a goal; it decomposes before it runs"),
    # Approvals (foundation section 10)
    Action("approval.approve", "Approve selected request", "Approvals", "a", "approvals",
           note="Sends the payload to its destination"),
    Action("approval.sendback", "Send back with a note", "Approvals", "r", "approvals",
           note="Nothing leaves the box"),
    # Router
    Action("router.focus", "Focus route tester", "Router", "/", "router"),
    # Audit
    Action("audit.focus", "Focus audit filter", "Audit", "/", "audit"),
    Action("audit.all", "Show all events", "Audit", "", "audit", href="/audit"),
    Action("audit.blocked", "Show blocked calls", "Audit", "", "audit", href="/audit?f=blocked"),
    Action("audit.allowed", "Show allowed calls", "Audit", "", "audit", href="/audit?f=allowed"),
    Action("audit.decisions", "Show human decisions", "Audit", "", "audit", href="/audit?f=decisions"),
)

BY_ID = {a.id: a for a in ACTIONS}
assert len(BY_ID) == len(ACTIONS), "duplicate action id"


def for_page(page: str) -> list[Action]:
    """Everything available on a page: global actions plus that page's own."""
    return [a for a in ACTIONS if a.scope in ("global", page)]


def nav_actions() -> list[Action]:
    return [a for a in ACTIONS if a.group == "Navigate"]


def payload(page: str) -> str:
    """JSON embedded in the page; console.js builds palette and `?` from it."""
    return json.dumps([asdict(a) for a in for_page(page)]).replace("</", "<\\/")


def key_conflicts() -> list[tuple[str, str]]:
    """Pairs of actions that share a key within one page's scope."""
    out = []
    for page in PAGES:
        seen: dict[str, str] = {}
        for a in for_page(page):
            if not a.keys or a.id.startswith("row."):
                continue
            if a.keys in seen:
                out.append((seen[a.keys], a.id))
            seen[a.keys] = a.id
    return out
