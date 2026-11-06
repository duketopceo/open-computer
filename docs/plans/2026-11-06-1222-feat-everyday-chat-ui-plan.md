---
title: Everyday chat UI - Plan
type: feat
date: 2026-11-06-1222
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
depth: deep
related: docs/plans/2026-11-04-001-feat-daily-driver-roadmap-plan.md; docs/plans/2026-11-01-001-feat-cloudflare-hosted-frontend-plan.md; docs/plans/2026-11-05-feat-pplx-computer-parity-plan.md; ROADMAP.md
---

# Everyday chat UI - Plan

**Target repo:** open-computer. Paths are repo-relative. Verified against `main` at `f4feee9` (2026-11-06). Plan-local ids: **R**, **KTD**, **U**, **F**, **AE**. Cite sibling plans as `daily-driver Un`, `cloudflare Un`, or `parity Un` — never bare `Un` across files.

**Product Contract preservation:** bootstrap (no prior brainstorm file). Sibling daily-driver Product Contract remains authoritative for streaming, stop/steer, tools, and durability; this plan supersedes only the everyday shell/composer IA choices named in KTD1–KTD3.

## Goal Capsule

- **Objective:** A general-audience owner can open Open Computer, type a multi-turn chat powered by OpenRouter, follow long-running work without operator clutter, and still reach every existing operator capability when they need it.
- **Means:** translate useful *observed* Perplexity Computer controls into OC-native navigation, composer, conversation, and long-run flows — without copying layout, branding, or terminology, and without adding features merely for parity.
- **Authority:** this Product Contract for everyday shell/IA; then `docs/plans/2026-11-04-001-feat-daily-driver-roadmap-plan.md` for turn/runtime depth; then `docs/plans/2026-11-01-001-feat-cloudflare-hosted-frontend-plan.md` for edge hosting constraints; then `ROADMAP.md`. Approval gate, deny-by-default egress, cost ledger, and spend rule stay unchanged. Local GPU models stay opt-in, never default.
- **Execution profile:** code, one PR per unit below, retargeted to `main`. This PR is documentation-only for the plan file.
- **Stop conditions:** stop and ask before shipping attachment upload, Artifacts/Projects pages, billing, teammates, a Search/Computer mode toggle, any deploy/wrangler change, or any unit that widens egress or weakens the approval gate. Do not merge feature PRs; Luke merges.

---

## Product Contract

### Summary

Open Computer already has a real chat shell on `/` (`ui/src/App.tsx`): composer with effort/Advanced and dictation, thread view with inline runs, chats plus earlier-runs cleanup, Agent jobs, offline/session banners, and operator pages under a collapsible Operator details block. What is still wrong for a daily driver is discoverability and IA weight — flat operator destinations compete with chat, daily-driver/OC-088 still prescribe a Perplexity-shaped Search/Computer toggle and nav copy, and conversation depth (SSE, stop/steer, inline approvals) is planned elsewhere but not yet on `main`. This plan owns the everyday shell translation and test bar; it defers backend-heavy capability to daily-driver and parity plans and forbids fake chrome.

### Problem frame

| # | Gap | Missing capability vs discoverability |
|---|---|---|
| G1 | Everyday vs operator surfaces are not clearly separated | Discoverability — Operator is already under `<details>` in `Sidebar.tsx`, but primary chrome still mixes Agent jobs / flat NAV weight with chat |
| G2 | Daily-driver U11 / OC-088 / parity row 1 assume a Search/Computer toggle | Product conflict — reject for parity; keep one-box chat |
| G3 | Composer model choice lives under Advanced; no attachment/artifact/project UI | Mixed — Advanced is intentional progressive disclosure; attachments/artifacts/projects are missing capability (defer, no fake buttons) |
| G4 | Replies still arrive via poll, not stream; no stop/steer; approvals mostly off-thread | Missing capability — owned by daily-driver U2–U4; this plan specifies UI contracts and tests so those land cleanly |
| G5 | Dictation fails opaquely when `GROQ_API_KEY` is unset (503) | Discoverability of a real dependency — honest unavailable state |
| G6 | Cloudflare offline/session story is plan-only; SPA already has link banners | Compatibility — keep relative `/api/*` and honest offline; do not change deploy config here |
| G7 | UI correctness is almost entirely Playwright; no `*.test.tsx` | Verification gap — add focused component tests where e2e is too coarse |

### Requirements

- R1. Everyday default chrome is chat-first: New chat, conversation list, composer, and long-run progress in-thread. Operator destinations stay reachable via progressive disclosure, not a always-visible bottom-left workspace menu of every page. (G1)
- R2. No Search/Computer mode toggle, no billing chrome, no Teammates surface in this product slice. (G2; session-settled)
- R3. Composer remains one required box to send. Effort levels stay the default spend control; model/budget overrides stay under Advanced (or an equivalent progressive disclosure). Local GPU entries, if shown later, are never the default. (G3)
- R4. Dictation remains available when the browser and server support it; when the server cannot transcribe, the mic control explains unavailability instead of failing only after a recording. (G5)
- R5. Conversation flows cover happy, empty, loading, failure, offline, expired-session, and permission-denied mic states without dead ends. Multi-turn persistence continues the same thread. (G4, G6)
- R6. Long-running work stays visible after navigation away and back (hash `#th_*` / `#goal_*`); reconnecting uses the existing poll path until daily-driver streaming lands, then SSE with poll fallback. Approvals remain hash-bound and single-use; this plan does not weaken the gate. (G4)
- R7. No fake buttons for Attachments, Artifacts, Projects, Customize, or Teammates. Those ship only with real APIs and ownership rules, phased separately. (G3)
- R8. Everyday UI keeps relative same-origin `/api/*` calls, CSRF on writes, and honest offline (no write queue) so cloudflare plan OC-092 can land without a chat redesign. This plan does not change Worker/deploy configuration. (G6)
- R9. Every feature-bearing unit ships tests with concrete input/action/outcome gates; async UI assertions wait for the relevant network response (pattern from #113). No live model calls in tests. (G7)
- R10. Visual polish uses existing Silver Lining tokens (`ui/src/index.css` / theme); light and dark screenshots at desktop and mobile widths accompany UI PRs (`tests/e2e/shots.mjs`).

### Key decisions

- KD1. Reject Search/Computer toggle, billing, and Teammates for parity. Governs R2. `(session-settled: user-directed — chosen over daily-driver U11 / OC-088 / parity row 1 wording: OC-native one-box chat)`
- KD2. Preserve operator power via progressive disclosure (Operator/Developer disclosure), not by deleting routes. Governs R1. `(session-settled: user-directed — chosen over removing operator pages)`
- KD3. Attachments and Artifacts/Projects are deferred until backend ownership exists; no decorative entry points. Governs R7. `(session-settled: user-directed — chosen over parity-driven nav stubs)`
- KD4. Local GPU never default; OpenRouter-backed cloud path remains the everyday default. Governs R3. `(session-settled: user-directed — chosen over local-first default)`

### Actors

- A1. Owner — authenticated human using the console on their device(s).
- A2. Orchestrator / workers — server-side; UI must not imply cloud-hosted backends.

### Key flows

- F1. New chat — empty home → compose with effort → send → thread selected → reply appears (poll today; stream later).
- F2. Multi-turn — open existing chat → follow-up without effort picker → one POST to that thread.
- F3. Long run — thread shows inline `RunView` → leave and return via hash → state refreshes; budget pause is visible.
- F4. Operator reach — expand Operator/Developer → open Approvals / Devices / etc. → return to chat without losing selection where possible.
- F5. Degraded link — offline or auth banner → composer disabled appropriately → recovery after network/session restore.

### Acceptance examples

- AE1. Logged-in owner on `/` sees New chat and composer; Operator destinations are not a permanent bottom-left dense menu. Covers R1.
- AE2. Composer has no Search/Computer control. Covers R2.
- AE3. Follow-up in an existing thread POSTs once to `/api/threads/<id>/messages` and shows the user message immediately (#113 wait pattern). Covers R5, R9.
- AE4. With Groq unset, mic shows unavailable before recording completes a doomed upload. Covers R4.
- AE5. Network failure shows offline status and does not queue sends. Covers R5, R8.
- AE6. Session 401 shows expired-session status and disables composer. Covers R5.

### Scope boundaries

- Not copied: Perplexity layout, branding, terminology, credit economy, hosted multi-user, Teammates, billing UI.
- Not built in this plan's feature units: web/browser tools, MCP client, subagents, plan-preview-before-spend, Temporal durable threads, Cloudflare Worker deploy (OC-092 implementation).
- Not changed: approval hash binding, egress defaults, spend rule / ledger semantics, forbidden branches/repos.

### Deferred to follow-up work

- daily-driver U1–U6, U8–U17 (durability, SSE, stop/steer, inline approvals, tools, hygiene depth, Settings/Usage pages, subagents, compaction, MCP/CLI, safety lanes) — consumed when ready; this plan only defines shell contracts and tests that those units must not break.
- daily-driver U7 — label/IA portions are superseded by KTD1 and everyday U2; React full-move leftovers (server-rendered operator pages still on `spa: false`) remain with daily-driver U7 / OC-089.
- parity plan capability units (web, browser, artifacts tools, connectors, memory, sharing) — remain in `docs/plans/2026-11-05-feat-pplx-computer-parity-plan.md`.
- Attachment upload + artifact/project ownership (phased backend) — see Planning Contract deferred attachment note.
- cloudflare U1–U7 implementation after Luke's explicit OK.

---

## Planning Contract

### Assumptions

- `docs/FEATURES.md` (2026-10-11) is stale for console maturity; code + `ROADMAP.md` + this research win.
- Repo has no root `AGENTS.md`; personal AGENTS rules still apply to authors (Luke attribution, no secrets/hostnames).
- `GET /api/models` exists on `main`; composer pickers beyond effort are still open (OC-087 partial).
- Logged-in visual verification of production UI was not performed in this planning pass; claims are code-and-test grounded.

### Translation matrix (Perplexity observed controls → OC)

Observed Perplexity controls are UI chrome only — not verified product behavior.

| # | Observed control | OC today (main) | Decision | Why |
|---|---|---|---|---|
| 1 | Central composer | `Composer.tsx` on home + in-thread | **Keep / polish** | Core everyday surface (U3) |
| 2 | Search / Computer selector | None; daily-driver U11 / OC-088 / parity row 1 proposed it | **Reject** | Session KD1; one-box chat; tools later without mode chrome |
| 3 | Model dropdown | Effort Low/Med/High + Advanced model/budget (`EffortPicker.tsx`) | **Adapt** | Keep effort primary; deepen Advanced honesty; optional catalog chip only when `GET /api/models` UX is ready without retiring spend clamp |
| 4 | Attachments | No upload UI in `ui/` | **Defer** | Missing capability; no fake button (R7) |
| 5 | Dictation / voice | Mic → `POST /api/transcribe` (Groq); 503 if unset | **Adapt** | Keep; add unavailable/permission states (U3) |
| 6 | New | “New chat” in `Sidebar.tsx` | **Keep** | Already primary |
| 7 | Computer | Nav label “Computer” → `/` in `nav.ts`; home is chat | **Adapt** | Prefer OC-native “Chat” / home wording over Perplexity “Computer” as primary everyday label; keep route `/` |
| 8 | Automations | `pages/Automations.tsx` + Operator nav | **Keep (operator)** | Progressive disclosure, not primary rail |
| 9 | Artifacts | Workspace registry actions + fixture viewers; no SPA Artifacts page | **Defer** | daily-driver U10 / parity U8; honest empty only if a destination is linked later |
| 10 | Customize | Theme via `theme.ts` localStorage; no Customize page | **Adapt / defer** | Theme toggle stays lightweight; full Settings → daily-driver U12 |
| 11 | Teammates | None | **Reject** | Out of product identity for this slice |
| 12 | Projects | None | **Defer** | OC-090 / daily-driver U8; no stub |
| 13 | Recents | Chats list + Earlier runs | **Keep / polish** | Rename/weight as Recents-equivalent without Perplexity copy |
| 14 | Account / plan controls | Session identity string; no billing | **Reject billing**; keep identity | No plan chrome |
| 15 | Effort / budget (OC-native, not PPLX) | Effort + Advanced + run spend | **Keep** | Spend safety is OC strength |
| 16 | Agent jobs / approvals / devices / sandboxes / router / audit / workspace | Present under Operator + pages | **Keep via disclosure** | R1 / KD2 |
| 17 | Offline / session expired | `App.tsx` LinkBanner | **Keep / extend tests** | Align with cloudflare honesty (R8) |
| 18 | Long-run progress / cost | `RunView.tsx`, agent jobs spend | **Keep**; deepen reconnect tests | Consume daily-driver streaming later |

### Everyday vs operator (target IA)

```text
Everyday (always reachable)
  New chat | Chats / Recents | [selected ThreadView + Composer]
  Agent jobs (badge when pending) — bridge to approvals, not full operator sprawl

Operator / Developer (progressive disclosure)
  Work, Overview, Goals, Automations, Skills, Sandboxes, Devices,
  Runs, Approvals, Router, Audit, Workspace, Dev trace
  — same routes as today; not deleted; not pinned open by default
```

### Conversation and long-run flows (contracts)

```text
Send (new)     → POST /api/threads → hash #th_* → poll GET thread until working clears
Send (follow)  → optimistic user row → await POST /messages response → poll
Long run       → message role run embeds RunView; budget pause copy from threads.py
Away / back    → hash restore; drop stale fetches (selectedRef pattern in App.tsx)
Approvals      → Agent jobs badge + Approvals page today; inline card = daily-driver U4
Stream later   → daily-driver U2 SSE; poll remains fallback (cloudflare U4 must list both)
```

### Key technical decisions

- KTD1. This plan supersedes daily-driver U7 nav *labels* and U11 Search/Computer / “retire effort for mode chrome” product wording for the everyday shell. daily-driver U7 still owns React full-move leftovers; U11's attach/model-chip work is re-scoped here without a mode toggle. `(session-settled: user-directed — chosen over implementing OC-088 as written)`
- KTD2. Do not introduce attachment or Artifacts/Projects UI chrome in U2–U5. A later backend phase may add them under explicit Luke approval.
- KTD3. Prefer extending Playwright in `tests/e2e/spa.e2e.mjs` plus a small Vitest/RTL suite under `ui/` for Composer/Sidebar pure states. Introducing Vitest is allowed in U6 because no `*.test.tsx` exists today. U2–U5 completion gates that cite `*.test.tsx` are e2e-only until U6 merges; after U6, those PRs may add matching component tests without rewriting gates.
- KTD4. Cloudflare readiness is a constraint checklist + tests (relative API, offline no-queue, CSRF), not an `edge/` implementation in this plan.
- KTD5. Disambiguate unit ids: this plan's U1… are **everyday-chat U***; never confuse with daily-driver or parity U-numbers.
- KTD6. Polish bar inherits daily-driver KTD6 (tokens only, axe 0, light/dark shots at 1440 and 390).

### Existing patterns to follow

- Optimistic send + `selectedRef` stale drop: `ui/src/App.tsx`
- Effort progressive disclosure: `ui/src/components/EffortPicker.tsx`
- Operator `<details>`: `ui/src/components/Sidebar.tsx`
- Link/offline/auth: `ui/src/lib/api.ts` `ApiError.kind` + `App.tsx` banner
- Follow-up race wait: `tests/e2e/spa.e2e.mjs` `@chat a follow-up…` (`waitForResponse` before assert) — #113
- Thread/effort HTTP contracts: `tests/opsview/test_threads.py`, `tests/opsview/test_effort_api.py`

### Dependencies (backend / API / data)

| Need | Status | Everyday plan action |
|---|---|---|
| `/api/threads`, messages, archive/delete/clear | Present | Consume |
| `/api/effort`, thread-locked plan | Present | Consume |
| `/api/transcribe` | Present; Groq required | Honest UI when 503 |
| `/api/runs`, events, agent-jobs, approvals | Present | Discoverability + tests |
| `/api/models` | Present | Optional chip later; not required for U3 |
| Thread SSE `/api/threads/<id>/events` | Missing (daily-driver U2) | UI contract only; cloudflare route parity must include when added |
| Attachment store / artifact ownership | Missing | Defer entire feature |
| Cloudflare Worker / `_edge/health` | Plan only | Constraint; no deploy here |

### System-wide impact

- Owners: cleaner daily chat.
- Operators/developers: same tools, one more click via disclosure.
- Edge/hosting: chat must stay proxy-friendly (R8); production implication noted without config edits.
- Sibling plans: ROADMAP OC-072/OC-088 text should be revised when U1 docs land so agents do not reintroduce the toggle.

---

## Implementation Units

### U1. Align docs and supersede conflicting shell wording

- **Goal:** Make ROADMAP / daily-driver / parity references agree that everyday shell rejects Search/Computer toggle and Perplexity-cloned primary nav, pointing here for IA.
- **Requirements:** R2, KD1, KTD1
- **Dependencies:** none
- **Files:**
  - modify: `ROADMAP.md` (OC-072, OC-088 rows — note supersession)
  - modify: `docs/plans/2026-11-04-001-feat-daily-driver-roadmap-plan.md` (short supersession notice on U7/U11)
  - modify: `docs/plans/2026-11-05-feat-pplx-computer-parity-plan.md` (row 1 deferral note)
  - test: none for prose-only — hygiene: `tests/unit/test_obs_open_source_hygiene.py` must still pass if touched paths are scanned
- **Approach:**
  1. Add one-line supersession pointers to this plan path.
  2. Do not rewrite sibling implementation units wholesale.
- **Test scenarios:**
  - Input: grep `Search or Computer` in those three files after edit. Action: read supersession notes. Expected: everyday rejection is explicit; no contradictory “must build toggle” without pointer here.
- **Acceptance criteria:** Reviewer can find a single source of truth for “no Search/Computer toggle” without reading chat history.
- **Completion gate:** Docs-only PR diff limited to plan + the three alignment edits (or this plan alone if alignment is deferred to the same PR as U2 — prefer same PR as U1).

### U2. Everyday navigation shell with progressive disclosure

- **Goal:** Chat-first sidebar: New chat, chats/recents, Agent jobs bridge; operator destinations only under a collapsed Operator/Developer disclosure; no fake Artifacts/Projects/Customize/Teammates items; mobile-friendly drawer behavior.
- **Requirements:** R1, R7, R10, AE1
- **Dependencies:** U1
- **Files:**
  - modify: `ui/src/components/Sidebar.tsx`
  - modify: `ui/src/lib/nav.ts`
  - modify: `ui/src/components/NavList.tsx` (if grouping helpers needed)
  - modify: `ui/src/App.tsx` (mobile shell wiring if required)
  - modify: `services/opsview/registry.py` only if nav labels must stay in sync (comment in `nav.ts`)
  - test: `tests/e2e/spa.e2e.mjs`
  - test (after U6): `ui/src/components/Sidebar.test.tsx`
- **Approach:**
  1. Keep existing routes and pages.
  2. Relabel primary home away from Perplexity “Computer” copy where it reads as everyday chat.
  3. Ensure Operator block defaults collapsed; remember collapse in `localStorage` if already patterned elsewhere.
  4. Do not add destinations without backends.
- **Patterns to follow:** current Operator `<details>`; `NAV` spa flags.
- **Test scenarios:**
  - Happy: open `/` → New chat visible → Operator summary collapsed → expand → Approvals link works.
  - Empty: no chats → “No chats yet.” copy remains.
  - Mobile: viewport 390 → primary chat usable; operator links reachable without permanent dense rail.
  - Keyboard: Tab to New chat and Operator summary; Enter toggles disclosure.
  - No fake: assert absence of buttons named Attachments / Projects / Teammates / Customize as primary nav.
- **Acceptance criteria:** AE1; axe 0 on home; light/dark shots attached.
- **Completion gate:** `tests/e2e` chat/nav selectors green; no new undeclared routes. Component tests optional until U6.

### U3. Composer honesty (effort, Advanced, dictation)

- **Goal:** Keep one-box send; effort + Advanced progressive disclosure; honest mic states for permission denial and server transcription unavailability; no attachment control.
- **Requirements:** R3, R4, R7, AE2, AE4
- **Dependencies:** U2 (layout stable)
- **Files:**
  - modify: `ui/src/components/Composer.tsx`
  - modify: `ui/src/components/EffortPicker.tsx`
  - modify: `ui/src/lib/api.ts` (optional capability probe or map 503 to typed error)
  - modify: `services/opsview/server.py` only if a cheap `GET` capability flag for transcription is cleaner than probing on mic click
  - test: `tests/e2e/spa.e2e.mjs`
  - test: `tests/opsview/test_effort_api.py` (unchanged contracts still pass)
  - test (after U6): `ui/src/components/Composer.test.tsx`
- **Approach:**
  1. Preserve effort on new chat only; follow-ups omit effort (existing).
  2. On mic activate, if server reports transcription unavailable, show status before/without relying on a completed recording upload.
  3. Map `NotAllowedError` (already) and 503 into distinct copy.
  4. Do not add Search/Computer or attach.
- **Test scenarios:**
  - Happy: new chat shows effort; send with Low works against fixture.
  - Advanced: open Advanced → change budget below max → options valid; above max → send disabled / validation message.
  - Follow-up: no effort picker (existing @chat test).
  - Mic permission denied: mock `getUserMedia` reject → alert copy; no upload.
  - Transcription unavailable: route `/api/transcribe` 503 → unavailable status; composer still types.
  - IME: Enter during composition does not send (existing behavior preserved).
- **Acceptance criteria:** AE2, AE4; no attachment control in DOM.
- **Completion gate:** Composer e2e green; effort API tests green. Component tests required only after U6 lands (KTD3).

### U4. Conversation surface states and multi-turn persistence

- **Goal:** Harden empty/loading/failure/offline/auth behaviors on the chat surface; keep multi-turn persistence; document reconnect-via-hash for long runs under poll.
- **Requirements:** R5, R6, R8, R9, AE3, AE5, AE6
- **Dependencies:** U3
- **Files:**
  - modify: `ui/src/App.tsx`
  - modify: `ui/src/components/ThreadView.tsx`
  - modify: `ui/src/components/DataState.tsx` (reuse on chat selection errors if helpful)
  - test: `tests/e2e/spa.e2e.mjs`
  - test: `tests/opsview/test_threads.py`
- **Approach:**
  1. Ensure selection 404, list load failure, and link banners have distinct accessible names.
  2. Preserve optimistic user message; assertions must `waitForResponse` on POST (never assert API calls only from “Working” visibility).
  3. Offline: disable send; do not enqueue.
  4. Auth: disable composer; show sign-in guidance (existing).
- **Test scenarios:**
  - Happy multi-turn: AE3 / #113 pattern.
  - Empty home: placeholder “What are we working on?” (or current copy) + composer enabled when link ok.
  - Loading: selecting a thread clears previous transcript before paint (no bleed).
  - Failure: 500 on get thread → selError message + retry path if present.
  - Offline: fulfill network error on listThreads → offline status; send disabled.
  - Expired session: 401 session → AE6.
  - Reconnect long run: fixture working run → navigate away → hash back → RunView still shows active/paused state from GET.
- **Acceptance criteria:** AE3, AE5, AE6; no flake from optimistic-send race.
- **Completion gate:** tagged `@chat` and `@errors` e2e green locally.

### U5. Long-run and approvals discoverability (without weakening the gate)

- **Goal:** Make awaiting-approval and budget-paused runs obvious inside everyday chat chrome using existing Agent jobs badge and run copy; do not replace daily-driver U4 inline approval card.
- **Requirements:** R6
- **Dependencies:** U4
- **Files:**
  - modify: `ui/src/components/RunView.tsx`
  - modify: `ui/src/components/AgentJobs.tsx` and/or `Sidebar.tsx` badge labeling
  - modify: `ui/src/components/ThreadView.tsx` (link/cta to Approvals or Agent jobs when a run awaits approval)
  - test: `tests/e2e/spa.e2e.mjs`
  - test: `tests/opsview/test_approvals.py` (gate unchanged)
- **Approach:**
  1. When embedded run is `AWAITING-APPROVAL` or budget paused, show clear status + navigation affordance to existing approval surfaces.
  2. Keep approve/deny on existing hash-bound APIs only.
- **Test scenarios:**
  - Awaiting approval fixture in thread → visible status + control that reaches approvals/agent-jobs.
  - Budget paused fixture → paused copy (from threads friendly strings) visible.
  - Permission: approve still requires existing CSRF + hash token paths (API test).
  - Failure: deny path still recorded; no auto-approve.
- **Acceptance criteria:** Owner can find a stuck approval without hunting the flat nav first.
- **Completion gate:** approvals e2e + API tests green; no gate semantic changes.

### U6. Component test harness + feature-to-test coverage matrix enforcement

- **Goal:** Add Vitest + Testing Library for Composer/Sidebar/App helpers; extend Playwright journeys for matrix rows still only weakly covered.
- **Requirements:** R9, R10, G7
- **Dependencies:** none for the harness itself; land before or immediately after U2 so later units can attach `*.test.tsx`. Feature units U2–U5 may merge on e2e alone first (KTD3).
- **Files:**
  - create: `ui/vitest.config.ts` (or equivalent)
  - modify: `ui/package.json` (`"test": "vitest run"` required)
  - create: `ui/src/components/Composer.test.tsx`
  - create: `ui/src/components/Sidebar.test.tsx`
  - modify: `.github/workflows/` only if an existing UI job is the natural place to add `cd ui && pnpm test` — otherwise open a follow-up issue named in the PR body and keep the local gate mandatory
  - modify: `tests/e2e/spa.e2e.mjs`
  - modify: `tests/e2e/shots.mjs` usage in PR checklist
- **Approach:**
  1. Keep Playwright as journey source of truth.
  2. Component tests cover pure UI state transitions (mic errors, operator collapsed, effort validation) without spinning opsview when possible.
  3. Merge gate for U6: `cd ui && pnpm test` must exist in `package.json` and pass locally; CI wiring is required in the same PR when a UI workflow already exists, else the PR body links a filed follow-up issue.
- **Test scenarios:**
  - Harness: `cd ui && pnpm test` runs component suite green.
  - Matrix: each row in Verification Contract coverage table has at least one automated test id/name cited in the PR.
  - Visual: shots at 1440 and 390 light/dark for home + thread.
  - a11y: axe 0 on home and thread fixtures.
- **Acceptance criteria:** Coverage matrix in Verification Contract has no “untested” required cell for shipped units.
- **Completion gate:** `cd ui && pnpm test` is in `package.json` and green; CI job updated or follow-up issue filed and linked.

### U7. Cloudflare compatibility constraints for everyday chat (no deploy)

- **Goal:** Encode OC-092 constraints into chat tests/docs so future Worker work does not force a redesign; do not add wrangler or GitHub deploy config.
- **Requirements:** R8
- **Dependencies:** U4
- **Files:**
  - modify: `docs/plans/2026-11-01-001-feat-cloudflare-hosted-frontend-plan.md` (cross-link: chat uses relative `/api/*`; future SSE paths include thread events when daily-driver U2 lands)
  - modify: `tests/e2e/spa.e2e.mjs` (assert fetch paths are same-origin relative in critical chat calls if not already)
  - test expectation: no `edge/` creation in this unit
- **Approach:**
  1. Document that dynamic prefix parity must include thread SSE when added.
  2. Keep offline semantics: writes disabled, never queued.
- **Test scenarios:**
  - Offline write: with offline banner, Send does not fire POST.
  - Auth/CORS mapping comment in `api.ts` remains accurate (manual review checklist item).
- **Acceptance criteria:** Plan + tests state production implications; no deployment configuration changed.
- **Completion gate:** Diff contains no `wrangler`, `edge/`, or deploy workflow changes.

---

## Verification Contract

### Commands (repo norms)

- UI typecheck/build: `cd ui && pnpm typecheck` / `pnpm build`
- Playwright: `cd tests/e2e && npm test` (and tagged runs per `tests/e2e/package.json`)
- Opsview: pytest targets `tests/opsview/test_threads.py`, `test_effort_api.py`, `test_approvals.py`
- Hygiene: `tests/unit/test_obs_open_source_hygiene.py`
- After U6: `cd ui && pnpm test` (script name fixed in that unit)

### Feature-to-test coverage matrix

| Feature / state | Component | API integration | Playwright journey |
|---|---|---|---|
| New chat + effort send | Composer.test | test_effort_api / test_threads | spa `@chat` |
| Follow-up same thread | — | test_threads | spa follow-up + `waitForResponse` (#113) |
| Effort Advanced validation | Composer/EffortPicker tests | test_effort_api | spa effort cases |
| Dictation permission denied | Composer.test | — | spa mic mocked |
| Dictation unavailable (503) | Composer.test | optional opsview | spa route mock |
| No Search/Computer / no fake attach | Sidebar/Composer tests | — | spa assert absence |
| Operator disclosure | Sidebar.test | — | spa expand Operator |
| Offline | — | api kind network | spa `@errors` offline |
| Expired session | — | 401 session | spa `@errors` session |
| Long-run reconnect | — | test_threads / runs | spa hash return |
| Approvals discoverability | — | test_approvals | spa agent-jobs / approvals |
| Mobile layout | — | — | spa 390 + shots |
| Keyboard/focus | Sidebar/Composer | — | spa Tab/Enter |
| Visual regression | — | — | shots.mjs light/dark |
| Cloudflare no-queue offline | — | — | spa offline send blocked |

### Deterministic async rules

- Never assert `api.calls` solely from optimistic UI (“Working”, user bubble).
- Prefer `page.waitForResponse` matching method+URL (see #113) or wait for server-driven DOM that cannot appear before the response.
- Drop stale fetches with `selectedRef` (already in App); tests must change hash mid-flight and expect no bleed.

---

## Definition of Done

### Global

- Translation matrix decisions implemented or explicitly still deferred with no fake UI.
- Everyday vs operator separation landed (U2) with progressive disclosure.
- R2/R7 honored (no toggle, teammates, billing, fake attach/artifacts/projects).
- Coverage matrix cells for shipped units are green.
- No secrets, personal hostnames, or bot co-author trailers; attribution Luke.
- No changes outside this open-computer checkout; leave unrelated local repos and branches `chore/agpl-3.0` / `wip/device-move-2026-10-27` alone.
- Production implication of OC-092 noted; deploy config untouched.

### Per-unit completion gates

Listed under each U-ID. A unit is done only when its gate is met on a PR against `main`.

### Decisions requiring Luke approval

- Shipping attachment upload or Artifacts/Projects chrome.
- Any change to approval gate, egress, or spend rule.
- Starting Cloudflare Worker implementation / Access policy (cloudflare plan stop conditions).
- Reintroducing a Search/Computer (or renamed) mode toggle.
- Making a local GPU model the default effort/catalog choice.

### Risks and rollout

| Risk | Mitigation |
|---|---|
| Sibling plans reintroduce Perplexity nav/toggle | U1 supersession notes; KTD1 |
| Streaming lands with different SSE path than Cloudflare docs | U7 cross-link; unify prefixes when daily-driver U2 ships |
| Vitest adds CI cost | Keep suite small; journeys stay in Playwright |
| Operator disclosure hides power users' habits | Remember expanded state; Agent jobs stays one click |
| Dictation probe adds latency | Cache capability per session; fail soft |

Rollout: ship U1 docs → U6 harness (or immediately after U2) → U2 shell → U3 composer → U4/U5 states → U7 constraints. Feature flags unnecessary if units stay additive. Prefer stacked PRs retargeted to `main`.

---

## Appendix

### Research breadcrumbs

- Repo research: verified chat shell, APIs, tests, missing attachments/artifacts/projects/teammates/settings pages, FEATURES.md staleness, SSE path tension between cloudflare goals events and daily-driver thread events.
- Plan reconcile: daily-driver U1–U17 open/partial map; OC-092 awaiting owner OK; parity row 1 conflict with no-toggle rule.
- #112 daily-driver plan merged; #106 Cloudflare plan merged (docs); #113 follow-up POST race fix merged; #122 parity plan merged.
- No Compound packs configured; no `docs/solutions/` hits required for this shell plan.

### Confidence self-check (planning)

- Material claims checked against `ui/src/*`, `services/opsview/server.py`, `tests/e2e/spa.e2e.mjs`, sibling plans on `main` @ `f4feee9`.
- Logged-in production pixels not visually verified — recorded as assumption.
- External Perplexity behavior beyond the provided observed-control list was not treated as fact.
