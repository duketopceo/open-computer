# Agent workspace

The main product screen: give an agent a goal in chat, watch it work, and keep what would leave the box under your
control. Spec: Silver Lining section 16 (`docs/design/silver-lining.md`, docketry PR #107). Wire format:
[event-stream.md](./event-stream.md). The existing console (`/`, `/runs`, `/approvals`, ...) stays the admin surface;
the workspace is at `/workspace` and the console sidebar links to it (`g g`).

## Try it with no sandbox and no model

    python -I tests/opsview/serve_fixture.py 8851 /tmp/oc-fx --replay
    # open http://127.0.0.1:8851/workspace  (the fixture accepts the cookie oc_sess=test)

Three recorded goals: a research report (web sources, markdown report, csv, svg, html, pdf), a coding task (diff, code,
diagram, image, archive, and two approvals, the second a high-risk push) and a goal that pauses at its $1.00 cap. A goal
starts when you open it. `?speed=0` plays instantly, `?hold=0` plays the recording without stopping for you, `?restart=1`
rewinds. In the default `hold` mode replay stops at each approval and at the cap and your real decision stands in for
the recorded one, so the approval path is exercised for real. Typing a new goal plays the closest recording and says so;
nothing is sent to a model.

## Layout (16.1, 16.2)

Left: goals grouped Needs you, Running, Done, Failed (240px, `[`). Center: title bar (status chip, elapsed, spend/cap chip,
context ring, always visible), timeline, then the approval dock above the composer. Right: seven tabs, 360px, resizable
280 to 560px, `]`. Below 768px it is chat only: the goal list is its own screen and the sidebar is a bottom sheet with
peek, half and full snap points, a handle, swipe down or Esc to close, and a strip of status, spend and badge chips
above the composer.

| # | Tab | Shows |
|---|---|---|
| 1 | Progress | Plan checklist, elapsed per step, one running step shimmers, Stop and Send note on sub-agent and sandbox rows |
| 2 | Sources | Deduplicated, citation counts, fetch age, **Not verified** for sources cited without being fetched |
| 3 | Artifacts | Grouped by type, version badge, unreviewed dot, lock when export needs approval |
| 4 | Changes | Diff against the sandbox's start, reviewable per hunk |
| 5 | Outbox | Every approval with who decided and when, plus destinations contacted. Badge turns amber while one is pending |
| 6 | Box | Template, limits, network allow-list, terminal tail |
| 7 | Cost | Spend against cap, tokens, per model, context window, Export run |

## Approvals (16.4)

The card docks above the composer and never steals focus; `y` jumps to it. Keys with the card focused: `a` allow once,
`Shift+A` allow for this goal (the scoped pattern is shown), `r` deny with an optional note, `f` full payload, `Esc` defers
without deciding. High risk renders Allow once as a secondary button and needs a second press; the server enforces it
(`428` without `confirmed`). Decisions are bound to the payload hash and single-use; a stale hash or a reuse is a `409`.
Esc, a reload and a reconnect never decide anything, and a deferred approval stays pending in the Outbox.

## Keys (16.7)

`/` composer, `Cmd/Ctrl+Enter` send, `[` `]` columns, `1`-`7` tabs while the sidebar has focus, `Shift+J/K` messages, `.` `.`
stop, `j/k` `Enter` `x` in lists, `g g` workspace, `Cmd/Ctrl+K` palette (goals, artifacts, sources and every command),
`?` lists every key and rebinds single-key actions. All of it is generated from `services/opsview/registry.py`, the same
registry as the console.

## Frontend approach

No-build ES modules served by the stdlib server, no third-party code, no CDN: about 49 kB gzipped for all JS and CSS
against the 180 kB route budget. Everything the screen shows is `fold(events)` (`static/projection.js`), rendered by keyed
reconcilers that replace a node only when its signature changes and keep focus when they do. That is what makes a reload,
a resume and a replay identical, and what keeps a 200-event burst from moving scroll, tab, focus or the hovered row.
Markdown, diff, CSV, code and Mermaid-flowchart renderers are small built-ins that build DOM with `textContent`, never
`innerHTML`. HTML artifacts run in `<iframe sandbox="allow-scripts">` with a response CSP of `default-src 'none'`, and the
page's own CSP has `frame-src 'self'`.

## Known gaps

- PDFs open in the browser's own viewer (an iframe); headless browsers don't render them.
- Office documents show a download prompt, not a preview; audio and video use the native player but have no fixture.
- Mermaid covers flowcharts (`flowchart`/`graph`, rectangles, rounded, diamonds, labels); other diagram types show their source.
- Notifications for background goals are in-page toasts only, deduplicated across windows with a stored key; system
  notifications and a per-event channel setting are not built.
- The side chat (read-only questions about the run) is not built.
- `Allow for this goal` is recorded and shown; enforcing it against later requests is the orchestrator's job (event-stream.md).
