# Frontend build plan — operator surface

Implements `docs/design/2026-10-11-frontend-design.md`. Frontend units
(F1–F4) slot against the backend roadmap (U5–U7); every unit ships only
views backed by real data.

## Ordering

```
U5 placement ─┐
              ├─► U6 orchestrator ─► U7 Khan+Temporal
F1 shell ─────┤
              └─► F2 task views ────► F3 approvals ─► F4 skills/automations
```

F1 needs nothing new — all its views already have real backends.
F2 needs U6 (task records). F3 needs gate pending-queue read (small U3
addition). F4 needs U7.

## F1 — shell + real-data views (now)

Refactor `services/opsview` from one page into shell + views. No new
backend dependencies — everything rendered exists today.

- Shell: left rail nav (or top bar — one dense row, not a sidebar;
  density over chrome), view keymap, `/` palette stub listing only
  implemented views
- `/ops` — current page unchanged (backward compat; oc.shippedit.dev
  keeps working)
- `/matrix` — matrix table + run action (extracted, same code path)
- `/fleet` — sandbox inventory from execplane: id, template, state,
  egress posture, TTL, parent task (none yet — column exists, renders
  `—`)
- `/gate` — audit tail view (read-only for now; actions arrive in F3)
- `/router` — route tester + ledger tail (move existing, add ledger read)
- `/` → redirect to `/ops` until F2

Keyboard: `g`+letter view switching, `j/k` scroll, `?` help overlay.
Palette lists views only — no action entries until F3 gives them real
targets.

Done when: every route serves real data, `?` documents all keys,
matrix/gate/fleet/router views match ops data exactly.

## F2 — task surfaces (with U6)

Depends on: orchestrator emitting task records (filesystem task dirs per
fsproto).

- `/` → task list (replaces redirect): STATE, GOAL, SUBTASKS n/m, COST,
  AGE, ENGINES; active first; `enter` opens task
- `/tasks/<id>` — plan tree (subtask rows: state, route/tier, engine,
  sandbox, cost, duration, dep indentation), event stream (fsproto
  outbox tail — poll first, SSE only if polling measurably lags),
  artifacts + audit slice
- `?view=thread` read-only variant
- Palette gains `new task` — but only if U6 exposes a submit path;
  otherwise it stays absent

Done when: a real U6 task renders end-to-end — plan tree matches the
fsproto task dir, event stream matches the outbox, costs match the
router ledger.

## F3 — approval actions (needs gate pending read)

Depends on: U3 extension — gate exposes pending-escalation queue as
data (today it lives inside the TUI console loop).

- `/gate` gains pending section: exact payload/diff inline, `a`/`r` on
  highlighted row, immediate audit append
- Palette gains `approve`/`reject` on highlighted context
- Self-approval stays impossible — approver identity comes from the
  perimeter (Tailscale/Access identity), not a form field

Done when: a real escalation created by `services/gate` appears,
renders its payload, and approving via keypress writes the audit row the
TUI console would have written (verify: same jsonl schema, readable by
`services/gate/audit.py`).

## F4 — skills + automations (U7 era)

- `/skills` — registry view once U7 has one
- `/automations` — Temporal schedules: cadence, next run, history;
  actions Run now/Pause when Temporal adapter exposes them

Not designed further until U7 exists — same rule: no view without a
backend.

## Cross-cutting requirements

- **Conformance tie-in**: each F-unit adds one check to the matrix —
  "view renders with empty data", "view renders real entity", so a UI
  regression is a matrix row, not a screenshot diff.
- **State vocabulary**: only the words in the design doc's normative
  list; a view introducing a synonym fails review.
- **Cost everywhere**: no F-unit ships without inline cost where the
  entity has cost (tasks, subtasks, routes).
- **No new dependencies**: stdlib + existing opsview pattern. Vanilla JS
  only, and only where keyboard behavior needs it.
- **Perimeter stays the auth**: Tailscale/Access identity; no login
  forms, no sessions in the app.
