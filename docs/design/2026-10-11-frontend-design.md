# Frontend design — open-computer workplace

Status: design. Scope: the human-facing surface for open-computer — the
workplace where goals are submitted, work is watched, and control points
are acted on. "OpenWebUI for open-computer": a self-hosted web app that
is the product's front door, not an observability afterthought.

## 1. Research: what Perplexity Computer's UI actually is

Surfaced from reviews, help-center docs, and selector-level harness notes
(2026-10-11):

- **Task-first, not chat-first.** The front door is `/computer/tasks` — a
  grid of task rows, not a chat history. A task is the unit of work.
- **Task run page** (`/computer/tasks/<id>`): plan as todo list, tool
  invocations inline, file artifacts attached. `?view=thread` is a
  read-only share view — same data, no controls.
- **"/" Command Panel**: slash palette over modes, skills, workflows —
  the verb surface. Validates our keyboard-palette requirement.
- **Skills page**: named reusable instruction sets deploying sub-agents.
- **Connectors page**: 400+ managed OAuth connectors.
- **Automations dashboard**: scheduled tasks grouped
  Active/Paused/Completed; Run now/Pause/Edit/Pin/Delete row actions.
- **Background execution**: close the browser, work continues. Thin
  shell — the UI renders events, the orchestrator owns intelligence.

### Documented weaknesses (reviews: awesomeagents 7.2/10, builder.io)

1. **Opaque credit costs** — no per-task/step cost; failed runs still
   burn credits.
2. **No debugging trail** — sandbox resets between runs; failures leave
   nothing to inspect.
3. **Black-box routing** — can't see which model ran a subtask or why.

Our determinism layer already produces the counter-evidence (audit log,
per-step cost ledger, engine attribution, policy diffs). The workplace's
job is to make that inspectability the product.

## 2. Position

An **OpenWebUI-shaped workplace for the orchestrator**: self-hosted, one
app, where the user both *does* the work (submit goals, steer subtasks,
approve escalations, collect artifacts) and *sees* the machinery (state,
cost, audit, sandboxes). Perplexity hides the machinery behind the task;
we render it — that visibility is the reason this exists at all.

Layout follows the proven shape (OpenWebUI, Computer, every serious
agent app): **left rail + main pane**.

- **Left rail**: goal input at top, task list below (grouped
  `ACTIVE` / `AWAITING` / `DONE`), nav section at bottom
  (Gate · Matrix · Fleet · Router · Ops).
- **Main pane**: the selected thing — a task run, the gate queue, the
  matrix — never a welcome screen. On load it selects the most recent
  active task, or the task list itself if none.

Design rules that don't move:

- **States are text**: every entity renders a literal state word —
  `QUEUED` `RUNNING` `BLOCKED` `AWAITING-APPROVAL` `DONE` `FAILED`
  `RECLAIMED` `PENDING` `APPROVED` `REJECTED` `UP` `DOWN`. Spinners may
  decorate, never substitute.
- **Cost is a column**: per task, subtask, and route decision — inline,
  always. Their #1 complaint is our default.
- **No fake anything**: a view ships only when its data source exists.
- **Thin shell**: the app renders events and state; it never runs agents.

## 3. Auth — passkey-gated (F-AUTH, blocks everything public)

The workplace is app-gated, not just perimeter-gated. Perimeter
(Tailscale identity / Cloudflare Access) may stack on top, but the app
itself authenticates.

- **Mechanism**: WebAuthn/passkey — platform authenticator first
  (fingerprint on this MacBook, phone via hybrid caBLE). No passwords,
  no TOTP seeds, no recovery codes.
- **Implementation**: `python-fido2` (Yubico reference lib) server-side —
  registration + assertion ceremonies, credential store in
  `~/.local/share/oc/credentials.json` (rp_id-scoped). The one new
  dependency, and it's the standard.
- **Session**: signed cookie (stdlib hmac), 12h, rolling. Unauthed
  requests get the passkey ceremony page — nothing else renders.
- **Enrollment**: first-run bootstrap — app refuses public bind until ≥1
  credential registered. Enrollment itself happens on loopback or behind
  the perimeter only.
- **Identity matters for authz too**: the gate approver's identity comes
  from the authenticated credential, not a form field — self-approval
  stays structurally impossible.
- **rp_id nuance**: passkeys are origin-scoped — a credential enrolled on
  `oc.shippedit.dev` won't assert on `omarchy-max.tailaf1559.ts.net`.
  Both origins get their own enrollment (register once per origin — the
  app detects unenrolled-authed-perimeter and offers enrollment).

## 4. Surfaces

| View | Path | Data source | When |
|---|---|---|---|
| Work | `/` | task list + selected task (U6) | U6 |
| Task run | `/tasks/<id>` | task record + fsproto outbox + audit | U6 |
| Gate | `/gate` | pending escalations + audit | now → actions in F3 |
| Matrix | `/matrix` | conformance harness | now |
| Fleet | `/fleet` | execplane → CubeSandbox | now |
| Router | `/router` | route tester + ledger | now |
| Ops | `/ops` | dense page (current) | now |
| Skills / Automations | `/skills`, `/automations` | U7 | future |

Until U6, `/` shows the task-list rail (empty, honest — "no orchestrator
yet") with the input bar **absent** — a goal box with no backend is a
fake control. Main pane defaults to `/ops` content.

### 4.1 Task list (left rail)

Rows, not cards. `STATE GOAL(n/m) COST` — dense, one line:

```
● RUNNING   rebuild docs site        $0.041
■ BLOCKED   deploy staging           $0.002
◐ AWAITING  port fsproto to rust     $0.000
```

### 4.2 Task run (main pane, U6+)

Top-to-bottom, single scroll:

1. **Goal + state header** — goal text, state word, running cost,
   elapsed, engines seen.
2. **Plan tree** — subtask rows: `state | title | route/tier | engine |
   sandbox | cost | duration`; dependencies as indentation (fsproto dep
   order).
3. **Event stream** — fsproto outbox tail: lease claims, writes,
   tombstones, reclaims. The debugging trail Computer lacks.
4. **Artifacts + audit slice** — produced files, gate rows for this task.
5. **Input bar** (bottom, only on task views) — steers the running task
   or starts a follow-up. This is goal-level input, not chat — every
   submission becomes an orchestrator directive with an audit row.

`?view=thread` — read-only share render.

### 4.3 Gate (`/gate`)

The differentiating view:

```
PENDING
T-4191 subtask-3  frontier — claude-opus     [diff ▾]  a=approve r=reject
T-4192 subtask-1  egress +pypi.org           [diff ▾]  a=approve r=reject

DECIDED (20)
07:41 approved  frontier — gpt-5.2        by luke@m1   2.1s
07:40 rejected  egress +raw.github…       by luke@m1
07:38 BLOCKED   pii prefilter — ssn       auto
```

Exact payload/diff inline. `a`/`r` on highlighted row. Decision appends
to audit immediately — the UI holds no undecided state. Approver shown
as the passkey-authenticated identity.

### 4.4 Ops views

Matrix / Fleet / Router / Ops — current semantics unchanged, moved into
the shell. Fleet rows show literal egress posture (`deny-all`,
`allow: h1,h2`); Router rows always name the engine leg
(`decider`/`jev`/`table`).

## 5. Interaction model

Keyboard-complete; mouse does nothing keys can't.

| Key | Action |
|---|---|
| `/` or `⌘K` | command palette — views + real actions |
| `g`+letter | `g w`ork `g g`ate `g m`atrix `g f`leet `g r`outer `g o`ps |
| `j`/`k` | move selection (rail and main-pane rows) |
| `a`/`r` | approve / reject highlighted pending escalation |
| `n` | new goal → focuses input bar |
| `enter` | open selected |
| `?` | key reference |

Palette entries only appear for implemented backends — no action with no
target.

## 6. Data contracts

- Auth ceremonies ← `python-fido2`; credentials ←
  `~/.local/share/oc/credentials.json`
- Tasks/subtasks ← U6 task records (fsproto task dirs)
- Gate ← `services/gate` pending queue + `audit.jsonl`
- Matrix ← `tests/conformance/harness.py`
- Fleet ← execplane → CubeSandbox API
- Router ← `services/router` + `router-ledger.jsonl`
- Live events ← fsproto outbox tail (SSE via stdlib chunked responses)

The app keeps no business data — credential store and session signing
key are the only local state.

## 7. Non-goals (v1)

- No generic chatbot — input bar submits goals/directives to the
  orchestrator, audited. Khan (U7) owns free-form conversation.
- No connector management UI — docketry's job.
- No multi-user roles — personal-first: passkey auth establishes *who*,
  not *which role*. Approver identity is recorded; there's one trust
  tier.
- No framework — stdlib + `python-fido2` only. Revisit only when a view
  genuinely can't be server-rendered (live DAG editing would be the
  first candidate).
