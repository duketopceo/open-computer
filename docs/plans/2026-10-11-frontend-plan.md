# Frontend build plan — workplace

Implements `docs/design/2026-10-11-frontend-design.md` — the
OpenWebUI-shaped workplace for open-computer: left rail + main pane,
passkey-gated, task-centric, every view behind a real backend.

## Ordering

```
F-AUTH (passkey gate)   — urgent: oc.example.com is live but bare
F1 shell + ops views    — no new backend deps
U5 placement ─┐
              ├─► U6 orchestrator ──► U7 Khan+Temporal
              └─► F2 work surface ──► F3 approval actions ─► F4 skills/auto
```

F-AUTH is first because the URL is already public — the workplace is
useless-to-dangerous open until the passkey gate exists.

## F-AUTH — passkey gate (first)

The app authenticates itself; perimeter stacks on top.

- `python-fido2` (Yubico reference) — registration + assertion
  ceremonies server-side; the one new dependency, and it's the standard
- Credential store `~/.local/share/oc/credentials.json`, rp_id-scoped
- Session: signed cookie (stdlib hmac), 12h rolling; unauthed → ceremony
  page only
- Enrollment: app refuses non-loopback bind until ≥1 credential;
  enrollment allowed on loopback or behind verified perimeter
- rp_id per origin — `oc.example.com` and the tailnet host get
  separate enrollments; app offers enrollment when perimeter-authed but
  credential-less
- Approver identity = authenticated credential handle (records into gate
  audit); self-approval stays structurally impossible

Done when: `oc.example.com` serves the ceremony page to strangers,
asserts with the platform passkey, session survives reload, and a
non-enrolled origin offers enrollment only behind the perimeter.

## F1 — shell + ops views

Left rail + main pane over `services/opsview` (stdlib, server-rendered).
No new backend dependencies — everything rendered already exists.

- Shell: rail (task list region — empty "no orchestrator yet" until U6,
  no input bar; nav: Gate · Matrix · Fleet · Router · Ops), main pane,
  `/` palette over implemented views
- `/ops` preserved verbatim (deep links keep working)
- `/matrix` — extracted matrix table + run action
- `/fleet` — sandbox inventory: id, template, state, literal egress
  posture (`deny-all` / `allow: hosts`), TTL
- `/gate` — audit tail, read-only for now
- `/router` — route tester + ledger tail
- Keyboard: `g`+letter, `j/k`, `?` help; `n`/`a`/`r` inert until their
  views exist

Done when: all routes serve real data behind the passkey session, `?`
documents shipped keys, matrix/gate/fleet/router render what ops
renders today.

## F2 — work surface (with U6)

Depends on: orchestrator emitting task records (fsproto task dirs).

- Rail task list live: `STATE GOAL(n/m) COST`, grouped
  ACTIVE / AWAITING / DONE
- `/` main pane = selected task run: goal+state header (cost, elapsed,
  engines), plan tree (deps as indentation), fsproto event stream
  (poll → SSE only if measurably needed), artifacts + audit slice
- `?view=thread` read-only render
- Input bar appears — submits orchestrator directives, audit-rowed;
  `n` focuses it, palette gains `new goal`
- Rail rows link `AWAITING` → `/gate` filtered

Done when: a real U6 task renders end-to-end — tree matches the task
dir, stream matches the outbox, cost matches the router ledger, and a
submitted directive produces a task + audit row.

## F3 — approval actions (needs gate pending read)

Depends on: U3 extension — gate exposes its pending queue as data
(today it lives inside the TUI console loop).

- `/gate` pending section: payload/diff inline, `a`/`r` on highlighted
  row, immediate audit append
- Palette gains `approve`/`reject` on highlighted context
- Approver = passkey credential identity, recorded in the audit row

Done when: a real escalation appears in `/gate`, renders its payload,
and `a` writes the identical audit row the TUI console writes (same
jsonl schema, readable by `services/gate/audit.py`).

## F4 — skills + automations (U7 era)

- `/skills` — registry view once U7 has one
- `/automations` — Temporal schedules: cadence, next run, history;
  Run now/Pause when the adapter exposes them

Not designed further until U7 exists.

## Cross-cutting requirements

- **Conformance tie-in**: each F-unit adds a matrix check — "view
  renders with empty data", "view renders real entity" — a UI regression
  becomes a matrix row, not a screenshot diff.
- **Auth regression check**: matrix row asserting unauthed requests get
  the ceremony page (no view leaks past the gate).
- **State vocabulary**: only the design doc's normative words; a
  synonym fails review.
- **Cost everywhere**: no F-unit ships without inline cost where the
  entity has cost.
- **Deps**: stdlib + `python-fido2` only. Vanilla JS where keyboard
  needs it, nothing else.
- **Session hygiene**: signing key in omaseal or
  `~/.local/share/oc/` with 0600 — never in the repo, never logged.
