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

PAGES = ("work", "overview", "goals", "automations", "skills", "sandboxes", "runs", "approvals", "router", "audit")

WORKSPACE = "workspace"  # the agent workspace page (/workspace); its actions have scope "workspace"
# Global console actions that mean something else on the workspace page (`c` creates a goal there).
NOT_ON_WORKSPACE = {"run.new", "goal.new"}


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
    Action("nav.automations", "Automations", "Navigate", "g a", href="/automations"),
    Action("nav.skills", "Skills", "Navigate", "g k", href="/skills"),
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
    # Agent workspace (docs/design/silver-lining.md section 16)
    Action("nav.workspace", "Agent workspace", "Workspace", "g g", href="/workspace"),
    Action("ws.new_goal", "New goal in the workspace", "Workspace", "c", WORKSPACE, note="Plays a recorded goal in replay mode"),
    Action("ws.focus_composer", "Focus the composer", "Workspace", "/", WORKSPACE),
    Action("ws.send", "Send message", "Workspace", "ctrl+enter", WORKSPACE),
    Action("ws.toggle_goals", "Toggle goal list", "Workspace", "[", WORKSPACE),
    Action("ws.toggle_side", "Toggle sidebar", "Workspace", "]", WORKSPACE),
    Action("ws.msg_next", "Next message", "Workspace", "shift+j", WORKSPACE),
    Action("ws.msg_prev", "Previous message", "Workspace", "shift+k", WORKSPACE),
    Action("ws.stop", "Stop the run", "Workspace", ".", WORKSPACE, note="Press twice to confirm. The sandbox is kept."),
    Action("ws.reveal_new", "Show new items", "Workspace", "n", WORKSPACE),
    Action("ws.select", "Select row", "Workspace", "x", WORKSPACE),
    Action("ws.focus_sidebar", "Focus the sidebar", "Workspace", "", WORKSPACE),
    Action("ws.export_run", "Export run as JSON", "Workspace", "", WORKSPACE, note="Downloads the event stream. Nothing leaves the box."),
    Action("ws.restart_replay", "Restart replay", "Workspace", "", WORKSPACE, note="Replay mode only"),
    Action("ws.raise_cap", "Raise cap and resume", "Workspace", "", WORKSPACE, note="Only when paused at the spend cap"),
    Action("ws.tab.progress", "Sidebar: Progress", "Sidebar", "1", WORKSPACE),
    Action("ws.tab.sources", "Sidebar: Sources", "Sidebar", "2", WORKSPACE),
    Action("ws.tab.artifacts", "Sidebar: Artifacts", "Sidebar", "3", WORKSPACE),
    Action("ws.tab.changes", "Sidebar: Changes", "Sidebar", "4", WORKSPACE),
    Action("ws.tab.outbox", "Sidebar: Outbox", "Sidebar", "5", WORKSPACE),
    Action("ws.tab.box", "Sidebar: Box", "Sidebar", "6", WORKSPACE),
    Action("ws.tab.cost", "Sidebar: Cost", "Sidebar", "7", WORKSPACE),
    Action("ws.approval_focus", "Go to the pending approval", "Approvals", "y", WORKSPACE),
    Action("ws.allow_once", "Allow once", "Approvals", "a", WORKSPACE, note="With the approval card focused. High risk needs a second press."),
    Action("ws.allow_goal", "Allow for this goal", "Approvals", "shift+a", WORKSPACE, note="With the approval card focused"),
    Action("ws.deny", "Deny, with an optional note", "Approvals", "r", WORKSPACE, note="With the approval card focused. Nothing leaves the box."),
    Action("ws.view_full", "View full payload or expand artifact", "Approvals", "f", WORKSPACE),
)

BY_ID = {a.id: a for a in ACTIONS}
assert len(BY_ID) == len(ACTIONS), "duplicate action id"


def for_page(page: str) -> list[Action]:
    """Everything available on a page: global actions plus that page's own."""
    return [a for a in ACTIONS if a.scope in ("global", page)
            and not (page == WORKSPACE and a.id in NOT_ON_WORKSPACE)]


def nav_actions() -> list[Action]:
    return [a for a in ACTIONS if a.group == "Navigate"]


def payload(page: str) -> str:
    """JSON embedded in the page; console.js builds palette and `?` from it."""
    return json.dumps([asdict(a) for a in for_page(page)]).replace("</", "<\\/")


def key_conflicts() -> list[tuple[str, str]]:
    """Pairs of actions that share a key within one page's scope."""
    out = []
    for page in (*PAGES, WORKSPACE):
        seen: dict[str, str] = {}
        for a in for_page(page):
            if not a.keys or a.id.startswith("row."):
                continue
            if a.keys in seen:
                out.append((seen[a.keys], a.id))
            seen[a.keys] = a.id
    return out
