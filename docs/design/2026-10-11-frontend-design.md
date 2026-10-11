# Frontend design — open-computer operator surface

Status: design. Scope: the human-facing surface for open-computer — what it is,
what it renders, what it deliberately does differently from Perplexity Computer.

## 1. Research: what Perplexity Computer's UI actually is

Surfaced from reviews, help-center docs, and selector-level harness notes
(2026-10-11):

- **Task-first, not chat-first.** The front door is `/computer/tasks` — a
  grid of task rows (title, state), not a chat history. A task is the unit
  of work; conversation is incidental.
- **Task run page** (`/computer/tasks/<id>`): plan rendered as a todo list,
  tool invocations inline, file artifacts (charts, PDFs, code) attached.
  `?view=thread` gives a read-only share view — same data, no controls.
- **"/" Command Panel**: slash palette over modes, skills, workflows — the
  verb surface. Validates the command-palette interaction already in our
  UX bar.
- **Skills page**: named reusable instruction sets that deploy sub-agents;
  My Skills / library tabs, searchable.
- **Connectors page**: 400+ managed OAuth connectors.
- **Automations dashboard** (`/computer/automations`): scheduled tasks
  grouped Active/Paused/Completed; row actions Run now/Pause/Edit/Pin/Delete.
- **Background execution model**: close the browser, the task continues;
  notifications on completion. The UI is a thin shell rendering events —
  intelligence lives in the orchestrator.

### Documented weaknesses (reviews: awesomeagents 7.2/10, builder.io)

1. **Opaque credit costs.** No per-task or per-step cost visibility;
   failed runs still burn credits. The single most-punished complaint.
2. **No debugging trail.** Sandbox resets between conversations; when a
   run fails there is nothing to inspect — no logs, no step trace, no
   state to audit.
3. **Black-box routing.** You can't see which model ran a subtask or why.

These three are exactly what our determinism rules already fix at the
*system* level (audit log, per-step cost ledger, explicit engine
attribution, egress/policy diffs). The UI's job is to make that
inspectability visible — it is the differentiator, not a feature.

## 2. Position

The surface is **mission control**, not a chat client and not a status
dashboard alone. Perplexity hides the machinery; we render it — because
our user's reason to run this instead of Computer is to see and control
what the orchestrator does.

- **Thin shell**: the UI renders events and state, never runs agents.
  Same architectural bet as Computer (their interface layer is swappable);
  ours is more honest about it.
- **States are text**: every entity renders a literal state word
  (`RUNNING` `BLOCKED` `AWAITING-APPROVAL` `DENIED` `PASS` `FAIL`
  `PENDING` `RECLAIMED`), not a spinner. Spinners may decorate, never
  substitute.
- **Cost is a column, not a popup**: every task row, every subtask, every
  route decision shows cost inline. Their #1 complaint is our table.
- **Approval is a first-class view**: gate escalations aren't
  notifications to dismiss — they're a queue with the exact payload/diff
  rendered, keyboard approvable.
- **No fake anything**: a view ships only when its data source exists and
  is real. No placeholder task list, no stubbed skills page.

## 3. Surfaces

One service (`services/opsview`, stdlib server-rendered, extends the
existing :8791 surface), one shell, named views:

| View | Path | Data source | Available when |
|---|---|---|---|
| Tasks | `/` | orchestrator task records (U6) | U6 lands |
| Task run | `/tasks/<id>` | task record + fsproto outbox + audit | U6 |
| Approvals | `/gate` | gate audit + pending escalations | now (U3 exists) |
| Matrix | `/matrix` | conformance harness results | now |
| Fleet | `/fleet` | execplane / CubeSandbox inventory | now |
| Router | `/router` | route tester + budget ledger | now (U4) |
| Ops | `/ops` | current dense page, unchanged | now |
| Skills | `/skills` | U7 skill registry | future |
| Automations | `/automations` | Temporal schedules | U7 |

`/tasks` does not ship until the orchestrator emits task records — an
empty state would be honest but the view itself would still be a fake
button. Until then `/` redirects to `/ops`.

### 3.1 Task list (`/`, U6+)

Dense grid, one row per task. Columns:

```
STATE      GOAL                              SUBTASKS  COST     AGE    ENGINES
RUNNING    rebuild docs site from source     3/7       $0.041   4m     decider,jev
BLOCKED    deploy staging to hetzner         1/4       $0.002   12m    decider
AWAITING   port fsproto to rust              0/5       $0.000   1m     —
```

- `BLOCKED`/`AWAITING` rows link straight to `/gate` filtered to that task.
- Sort: active first, then by age. No filters in v1 — density over
  controls.

### 3.2 Task run page (`/tasks/<id>`, U6+)

Three panels, top to bottom, single scroll — no tabs:

1. **Plan tree** — decomposition as a nested list. Each subtask row:
   `state | title | route/tier | engine | sandbox id | cost | duration`.
   Dependencies render as indentation, matching fsproto dep ordering.
2. **Live event stream** — fsproto outbox for the task, tailed. Lease
   claims, writes, tombstones, reclaims as timestamped lines. This is the
   debugging trail Computer doesn't have.
3. **Artifacts + audit** — files produced (from the task workspace) and
   the gate audit slice for this task id.

`?view=thread` — read-only render of the same page for sharing (their
pattern, same mechanism: query flag, no separate share endpoint).

### 3.3 Approvals (`/gate`)

The differentiating view — what Computer doesn't expose at all:

```
PENDING ESCALATIONS
T-4191  subtask-3  frontier call — claude-opus   payload: [diff ▾]   a=approve r=reject
T-4192  subtask-1  egress allowlist +pypi.org   policy diff: [diff ▾] a=approve r=reject

DECIDED (latest 20)
07:41:12  approved  frontier — gpt-5.2   by luke   2.1s to decide
07:40:03  rejected  egress +raw.githubusercontent.com   by luke
07:38:44  BLOCKED   pii prefilter — ssn candidate   auto
```

- Pending items render the **exact payload/diff** inline (expandable);
  approving what you can't read is the failure mode this exists against.
- `a`/`r` act on the highlighted pending row. Every decision appends to
  the audit log immediately — the UI never holds undecided state.

### 3.4 Matrix (`/matrix`)

Current matrix table moved to its own view, unchanged semantics: rows
R1–R7 + ROUTER, `PASS`/`FAIL`/`PENDING`, timestamp of last run, "run"
action. The confidence surface stays one keystroke away.

### 3.5 Fleet (`/fleet`)

Sandbox inventory: id, template, state, egress posture
(`deny-all` / `allow: host1,host2`), TTL remaining, parent task.
Empty allowlist renders as literal `deny-all`, not "restricted" — the
exact policy is the point.

### 3.6 Router (`/router`)

Current route tester, plus below it the budget ledger tail: last N
decisions with engine, confidence, cost, running daily total. The engine
column always names the leg that answered (`decider`/`jev`/`table`) —
routing is never a black box here either.

## 4. Interaction model

Keyboard-complete; the mouse does nothing the keys can't.

| Key | Action |
|---|---|
| `/` | command palette — views, actions (run matrix, new task, approve…) |
| `g` then letter | go to view (`g t`asks, `g g`ate, `g m`atrix, `g f`leet, `g r`outer, `g o`ps) |
| `j`/`k` | move row highlight |
| `a`/`r` | approve / reject highlighted pending escalation |
| `enter` | open highlighted row |
| `?` | keyboard reference |

The palette mirrors Computer's "/" panel — same discovery pattern —
but its entries are verbs against real backends; an action that has no
backend doesn't appear.

## 5. States vocabulary (normative)

Every entity uses exactly these words — views must not invent synonyms:

- Task/subtask: `QUEUED` `RUNNING` `BLOCKED` `AWAITING-APPROVAL`
  `DONE` `FAILED` `RECLAIMED`
- Gate item: `PENDING` `APPROVED` `REJECTED` `BLOCKED` (auto-deny)
- Sandbox: `RUNNING` `PAUSED` `KILLED`
- Check: `PASS` `FAIL` `PENDING`
- Service: `UP` `DOWN`

`PENDING` is reserved for "not yet evaluated" — never for "waiting on a
human" (that's `AWAITING-APPROVAL`). The distinction matters: pending is
ambient, awaiting is actionable.

## 6. Data contracts

Views read only real services — the UI adds no persistence of its own:

- Task/subtask state ← orchestrator records (U6; filesystem-backed via
  fsproto task dirs)
- Gate pending/decided ← `services/gate` audit.jsonl + pending queue
- Matrix ← `tests/conformance/harness.py` run output
- Fleet ← execplane client → CubeSandbox API
- Router ← `services/router` decision + `router-ledger.jsonl`
- Events ← fsproto outbox tail (SSE for live updates when needed;
  stdlib chunked responses suffice — no framework dependency)

## 7. Non-goals (v1)

- No chat interface — talk to tasks, not at them. Khan owns the
  conversational front door (U7); this surface is for watching and
  controlling.
- No connector management UI — that's docketry's eventual job; connectors
  here are credentials at the execplane boundary, not a settings page.
- No multi-user, no auth beyond the perimeter (Tailscale identity /
  Cloudflare Access) — personal-first.
- No framework migration — stdlib server-rendered until a view genuinely
  can't be (live DAG editing would be the first candidate; nothing else
  comes close).
