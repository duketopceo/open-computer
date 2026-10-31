---
title: General-market console UX - Plan
type: feat
date: 2026-10-31
execution: code
---

# General-market console UX - Plan

## Goal

Make the console something a non-technical person can use without a glossary. The reference is Perplexity Computer:
one task box, an effort picker under it (already shipped in #95), and a short left nav. Everything an operator or
developer needs stays, but sits behind a **Developer tools** switch instead of in the default nav.

Success looks like this: a new user opens the console, sees one question and one box, starts a task, sees the tasks
list, and is asked "needs you" when something has to wait for them. They never see the words *tier, matrix, egress,
conformance, ndjson, payload, router, ledger, sandbox, placement* unless they turn Developer tools on.

**Out of scope:** the Approvals card content (another workstream owns `ui/src/pages/Approvals.tsx` until it merges),
the hostname cleanup, porting the last Python-rendered pages (`/workspace`, `/runs/new`, the 404 page) to React (that
is [the full-move plan](2026-10-30-003-feat-react-full-move-plan.md)), new backend features, any change to the spend
rule or the approval gate. No URL is removed: every existing route keeps working.

## Audit (Step 1, verified on `main` at `c648765`)

Method: fixture server (`tests/opsview/serve_fixture.py`), `pnpm build`, Playwright full-page shots at 1440 and 390 of
all 14 routes, plus the visible text of each. Shots: `/workspace/oc-ux/before/*.png` on the box; the four key ones are
committed under `docs/screenshots/general-market-ux/`.

### Findings

| # | Finding | Where | Severity |
|---|---|---|---|
| A1 | **13 nav items, two different navs.** Home shows `Computer`, `Agent jobs`, a Runs list and an "Operator" block of 12 links; every other React page shows a flat list of 13 items (`Computer`, `Work`, ...). Python pages (`/workspace`, `/runs/new`, 404) show a third nav with keyboard hints ("g w"). | `Sidebar.tsx`, `PageShell.tsx`, `views.py` | High |
| A2 | **The home page is unusable at 390 px.** A fixed 240 px sidebar leaves about 150 px for the page: the heading wraps to five lines, the composer text is clipped, and the effort picker stacks vertically. No menu button exists on this page. | `App.tsx` + `Sidebar.tsx` | High |
| A3 | **Duplicate and dead-end destinations.** `Work` links to `/work`, which redirects to `/`. `Goals` and `Runs` are the same page (`/goals` renders `Runs`, titled "Runs"). `Overview` repeats `Runs` and the audit list. The Runs empty state says "Describe a goal on the Work page", a page that no longer exists. | `nav.ts`, `main.tsx`, `Runs.tsx` | High |
| A4 | **Jargon on the first screen.** "It decomposes, routes, places and runs each step - here you watch the machinery, not just the answer." The caption shows a raw model id (`google/gemini-2.5-flash`) and "per run". | `App.tsx`, `EffortPicker.tsx` | High |
| A5 | **Raw internals as content.** Audit rows print `cloud_call_blocked`, `payload_hash=959db5...`, `pii_categories=[]`. Overview prints event names. Skills prints env var names ("set OPENROUTER_API_KEY") and host:port pairs. Router asks for an "Utterance". | `Audit.tsx`, `Overview.tsx`, `Skills.tsx`, `Router.tsx` | High |
| A6 | **Developer concepts in the default nav.** Router, Audit, Sandboxes, Devices, Skills (capability health), Agent jobs and Workspace are all one click from a first-time user. | `nav.ts` | High |
| A7 | **Unfriendly state words.** `QUEUED`, `LEASED`, `AWAITING`, `FAILED` in upper-case mono; "Paused - budget"; "parked waiting for a device". | `primitives.tsx`, `RunView.tsx`, `Devices.tsx` | Medium |
| A8 | **Automations asks for "Every N minutes"** with a number box and says "durable Temporal schedules". People think in hourly, daily, weekly. | `Automations.tsx` | Medium |
| A9 | **Tables are the only list pattern**, so on a phone you scroll sideways (`min-w-[34rem]`). Tasks, approvals and automations are lists of things a person acts on, not data to scan. | `DataTable.tsx` | Medium |
| A10 | **Empty states explain the system, not the next step.** "Create one above - a goal that runs on a cadence." "Start a device agent to register one." No first-run help, no check that the computer is ready to run a task. | all pages | Medium |
| A11 | **Page titles, product name and casing drift.** Tab title `Overview - open-computer`; brand `Open Computer`; Python pages use `open-computer`. | `PageShell.tsx`, `views.py` | Low |
| A12 | **No global "needs you" signal.** The pending count shows only on the home page's Agent jobs button. Approvals waiting elsewhere are invisible from other pages. | `Sidebar.tsx` | High |
| A13 | **DESIGN.md contradicts the shipped console.** It prescribes dark "Flight Strip", monospace, amber, no shadows, and rejects chat UI. The console ships a light paper canvas, teal, a serif ask, soft shadows (Perplexity Computer shape, per `tailwind.config.ts`). | `DESIGN.md` | Medium |

### Screen by screen

| Route | Verdict | Target |
|---|---|---|
| `/` Computer | Keep, simplify copy, fix mobile | **New** |
| `/runs`, `/goals` | Same page, two names | **Tasks** (one route, `/goals` redirects) |
| `/approvals` | Keep. Content is another workstream's | **Needs you** (label and empty state only, after that PR merges) |
| `/automations` | Keep, friendlier cadence | **Automations** |
| `/overview` | Redundant for people | Developer: **Activity summary**. Spend moves to the sidebar footer |
| `/skills` | Capability health is a setup check | Developer: **Setup status**. A plain "not connected yet" banner on New |
| `/sandboxes`, `/sandboxes/:id` | Operator | Developer: **Workspaces** |
| `/devices` | Operator today | Developer: **Computers** (see decision D1) |
| `/router` | Operator | Developer: **Model chooser** |
| `/audit` | Operator | Developer: **Activity log** |
| `Agent jobs` (view on `/`) | Operator | Developer: **Background agents** |
| `/workspace` (Python) | Operator | Developer: **Agent workspace** |
| `/runs/new` (Python) | Operator self-check | Developer: **Self-check** |

### Jargon list

Terms found in user-visible text, with where and the replacement (the full glossary is below):
*tier/effort level, matrix, determinism, conformance, microVM, sandbox, egress, "leaves this box", payload,
payload hash, PII, ndjson, Temporal, durable, decompose, route, place/placement, router, ledger, utterance, lease,
parked, control plane, capability surface, fail-closed, escalation gate, drive loop, operator, agent job, goal, run,
cloud_call_blocked / cloud_call_allowed / human_decision, env var names, model ids, `host:port`.*

## Direction

**Visual thesis** (extends the shipped system, per ce-frontend-design Module C): a calm paper canvas, one teal
accent for "this is yours to act on", a serif for the single question on the page, plain sans everywhere else. It
should feel like a notebook with one pen on it, not a cockpit.

**Content plan:** *New* is one question, one box, one effort picker, three starter ideas. *Tasks* is a plain list of
what you asked for and where it stands. *Needs you* is the only place a person is asked to decide. *Automations* is
"do this again every day". Nothing else is on the first level.

**Interaction plan:** (1) the sidebar collapses into a slide-in menu under 768 px with focus return and Esc;
(2) the composer is the first thing under the heading at every width; (3) a task starting fades its card up (the
existing `fade-up`), reduced-motion respected; no other new motion.

Cards stay only where the card is the interaction (a task, an approval, a starter idea). Stat tiles and the
decorative Overview cards go.

## Information architecture

Primary nav (5 entries, top to bottom):

| Label | Route | Notes |
|---|---|---|
| **New** | `/` | The task box. Current task view stays here (`/#run_id`) |
| **Tasks** | `/runs` | Replaces Runs, Goals, Work, Overview for people |
| **Needs you** | `/approvals` | Count badge on every page. Counts both approval queues |
| **Automations** | `/automations` | |
| **Recent** | (list, not a page) | The 8 latest tasks under the nav, like Perplexity Recents. Click opens `/#run_id` |

Sidebar footer: **Spent today $0.00 of $1.00** (from `/api/overview`, plain, no page), a **Developer tools** switch,
the signed-in identity.

**Developer tools** (off by default, stored in `localStorage` as `oc-developer`, no server state): adds a
"Developer" group under the nav with Activity summary, Background agents, Setup status, Workspaces, Computers, Model
chooser, Activity log, Agent workspace, Self-check. It is a visibility switch, not a permission boundary: every URL
still works and the auth and approval gates are unchanged. If you open a developer URL directly, the group shows
itself so you never land on a page the nav does not know about.

One shell: a single `AppShell` replaces `Sidebar` (home) and `PageShell` (everything else). The home page keeps its
run-focused body. Python-rendered pages keep their own shell until ported; their nav labels follow this plan in the
port, not before.

### Mobile

Under 768 px the sidebar becomes a top bar (product name, **New** shortcut, **Menu**) and a slide-in panel with the
same nav and footer. The panel closes on route change, on Esc, and on outside tap, and returns focus to the Menu
button. Lists are stacked rows, not sideways-scrolling tables. No horizontal scroll at 390 px on any route (already
asserted by the e2e a11y tests for the ported pages; extended to `/`).

### Onboarding and empty states

| Place | Today | New |
|---|---|---|
| New, first run, nothing connected | the same box, fails later | A one-line banner: "Tasks run on this computer only until you connect a model." with a **Set up** link (Developer: Setup status). Hidden when ready |
| New, starters | ASCII editor, dataset map, haiku | Three everyday ideas in plain words; same click-to-fill behavior |
| Tasks, empty | "Describe a goal on the Work page" | "No tasks yet. Start one from New." + **New task** button |
| Needs you, empty | (other workstream) | "Nothing needs you. A task waits here before it sends anything out or spends money." |
| Automations, empty | "Create one above" | Example chips ("Every morning: summarize my notes") that fill the form |
| Tasks, running | uppercase state | "Working", "Waiting for you", "Done", "Didn't finish" |

## Plain-language glossary

The table is the source of truth. It becomes `ui/src/lib/words.ts` (one place for user-facing words, next to
`friendly.ts`), and an e2e test asserts the default views contain none of the banned terms.

| Technical term | User term | Notes |
|---|---|---|
| goal, run | **task** | one word everywhere. Button: **Start** (was Run) |
| Run it? / Run matrix | **Start this task?** | Self-check keeps "Run self-check" |
| effort level, tier | **effort** (Low, Medium, High) | already shipped. Caption drops the model id; the id moves to a tooltip and Advanced |
| per-run budget / cap | **spending limit** | "up to $0.10" |
| budget halt, "Paused - budget" | **Paused at your spending limit** | with **Raise limit** later |
| approval, escalation gate, human gate | **needs you**, **check first** | verbs: **Allow**, **Don't allow** |
| egress, "leaves this box" | **sends data out**, **leaves this computer** | name the destination in words |
| payload | **what will be sent** | |
| PII | **personal details** (name the kinds: email, SSN) | |
| sandbox, microVM | **workspace** (safe, separate area it works in) | |
| matrix, determinism, conformance | **self-check** | developer only |
| router, route, utterance, decision ledger | **model chooser**, **past choices** | developer only |
| audit, append-only | **activity log** | developer only |
| `cloud_call_blocked` / `cloud_call_allowed` / `human_decision` | **Blocked a request to a cloud model** / **Sent a request to a cloud model** / **You approved/declined** | map in `words.ts`; unknown events fall back to title case |
| Temporal, durable schedule | **scheduler** (just "Automations") | |
| Every N minutes | **Every hour / day / week / custom** | stored value is still minutes |
| QUEUED, LEASED, RUNNING, AWAITING, FAILED, DONE | **Waiting to start, Working, Working, Waiting for you, Didn't finish, Done** | `StateWord` |
| device, parked | **computer**, **waiting for the computer to come back online** | |
| control plane | **workspace service** | |
| capability surface, skills (health) | **setup status** | env var names only under Developer |
| agent job | **background agent** | developer only |
| ndjson, hash, ids (`run_ab12`, `ap_1c1d`) | hidden; shown small under Developer or on copy | |
| operator | (removed) | |

Rules for new copy: sentence case; verbs for buttons ("Start", "Allow"); say what happens and what it costs before it
happens; never show an id, a model id, a path, or an env var name outside Developer tools; no "AI commentary"
("here you watch the machinery").

## Design system changes (`DESIGN.md`)

`DESIGN.md` is "Flight Strip": dark, monospace, amber, no shadows, a proposal dated 2026-10-10 that predates the
React console. The shipped console went a different way on purpose (paper, teal, serif ask, Perplexity Computer
shape). This plan updates `DESIGN.md` rather than fighting it:

- Add a **Console (people)** section at the top: the paper/teal tokens from `tailwind.config.ts`, the serif-for-the-ask rule,
  the card rule above, sentence case, and the glossary rule. Status becomes "console = this section; CLI/TUI and the
  Developer views = Flight Strip".
- Keep Flight Strip for the CLI, the approval TUI, and Developer tools: state is always a glyph plus a word, accent means
  "live or needs you", 4.5:1 contrast. These carry over to the console unchanged.
- The old "Reject: chat-bubble UI" stays true (the task box is not a chat thread).

## Implementation sequence

Each unit is one PR, squash-merged on green CI (python, console e2e + axe). Each ships with e2e and axe at 1440 and
390, light and dark where the existing tests do.

| PR | Scope | Verifies |
|---|---|---|
| **0** | This plan, the audit, the `DESIGN.md` update, before screenshots | docs only |
| **1** | **IA + nav + mobile.** `AppShell`, new nav (`nav.ts` primary/developer), Developer switch, Recent list, Needs-you badge, spend footer, mobile top bar and slide-in menu, `/goals` and `/work` redirect to Tasks and New, home works at 390 | `@nav` tests rewritten for the new nav; home axe + no horizontal scroll at 390; Developer switch persists and reveals developer routes; badge appears on a non-home page |
| **2** | **Copy pass.** `words.ts` glossary, state words, event names, caption without model id, confirm card, Tasks/Automations/Overview/Skills/Sandboxes/Devices/Router/Audit copy, plain cadence picker, document titles | e2e asserts banned terms absent from default views; developer views may keep them |
| **3** | **Empty states + onboarding.** Setup banner, everyday starters, Tasks as stacked rows, empty-state copy and examples, Needs-you empty state | e2e per empty state; axe |
| **4** (after the Approvals PR merges) | `Needs you` label, plain approval copy, mobile card layout | rebased on main, axe |

PR 0 can merge first because it is docs. PR 1 must land before 2 and 3 so they edit the new shell once.

## Risks and decisions

- **The `@nav` and `@agents` e2e tests drive the old nav** (Agent jobs button, Operator links). PR 1 updates them; it does not delete
  coverage. The Agent jobs view moves behind Developer but keeps its tests.
- **Python pages keep the old shell** for now, so a person can still land on a different-looking page via `/workspace`
  or a 404. Accepted until the full move finishes; the 404 and `/runs/new` are the next ports after PR 3.
- **No hiding as security.** Developer tools is presentation. Do not describe it as access control.
- **Decisions for Luke** (defaults chosen so work is not blocked): D1 is five primary items right, or is Computers
  (paired devices) a primary item for people with their own machines? Default: developer-only. D2 rename "Tasks" or keep
  Perplexity's "Computer" for the task list? Default: Tasks. D3 should Needs you stay top-level (it is our safety story)? Default: yes.
