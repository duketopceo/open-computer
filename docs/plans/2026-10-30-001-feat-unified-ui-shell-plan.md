# Plan: Unified UI shell — one product surface, agent-shell polish, onboarding

## Problem

Two UIs ship today and users see both:

- **React shell** (ui/, oc.example.com) — Work/Goals/Runs, dark sidebar,
  the "good" UI.
- **Python opsview** (services/opsview/views.py) — Devices, Sandboxes,
  Approvals, Router, Audit, Overview, Automations, Skills — a different
  look and vocabulary. The React sidebar links straight into it, so one
  click drops users into the "wrong" app.

Plus: the run/step surfaces leak internals (`goal_e0f940b1`,
`CancelledError`, task hashes, `cloud-OpenRouter`) and there's no
first-run onboarding.

## Goals

1. One shell: every nav destination renders inside the React app with
   the same design system; no visible seam between "work" and "ops".
2. Chat-legible runs: human words for every internal name; heavy payloads
   collapsed; live step activity visible while a step runs.
3. Onboarding: first-open state that explains goal→plan→run→approval in
   one screen and offers a safe first goal.

## Already shipped (PR #83)

- `RunView`: results >8 lines / HTML / JSON collapse behind
  "Output — N lines"; short results render as prose.
- `lib/friendly.ts`: human labels for error kinds, placements, engines.
- `views.py` `_fail_item`: same label words; subtask id replaces task hash.

## Units

### U1 — Port operator pages to React (biggest lift)

`views.py` is ~1290 lines / ~27 renderers. Port order by daily use:

1. `/runs` (run list + run summary panel incl. worker_failures — already
   friendlified server-side, port verbatim first)
2. `/approvals` (the gate — approve/send-back must stay POST+CSRF)
3. `/devices`, `/sandboxes`
4. `/audit`, `/router`
5. `/overview`, `/automations`, `/skills`

Each page needs a `/api/...` JSON surface (some exist: `/api/runs`,
`/api/runs/:id`; approvals/devices/audit need endpoints). Keep `views.py`
serving the pages until the React route ships, then redirect.

Exit: every sidebar href renders in the React shell; opsview only serves
API + legacy redirects.

### U2 — Agent-shell niceties (the "little things")

- Live step heartbeat: elapsed-time ticker on LEASED/RUNNING rows.
- Step activity events: worker writes "calling model" / "exec in
  sandbox" markers into the run audit; UI renders them under the step.
- Run resume/retry affordance on failed runs (dep-fail hint already
  says "rerun or fix and resume" — make it a button).
- Result streaming for model steps (bigger; can start as a poll).
- Friendly step ids: show title, not `t1`/`task-1` ids (mostly done via
  titles already; drop the id display entirely).
- Errors in plain words everywhere (partially done in #83).

### U3 — Onboarding

- Empty state on first open: one sentence on what it does, a "try:"
  suggestion list (the geojson goal that actually works), and a note on
  what approvals mean.
- First blocked-run explainer: when a step blocks on unapproved egress,
  show a one-click "approve this host" affordance (gate already exists —
  surface it).

## Non-goals

- No redesign of the design system itself — reuse existing tokens.
- No new backend semantics; this is presentation + thin API surface.

## Risks

- Porting approvals without care breaks the human-approval gate (must
  keep POST + CSRF + single-use semantics).
- Dual-serving pages during migration can drift — ship each page behind
  its own route and redirect as each lands.
