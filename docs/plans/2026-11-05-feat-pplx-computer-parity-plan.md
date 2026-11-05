---
title: Perplexity Computer parity - Plan
type: feat
date: 2026-11-05
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
depth: standard
related: ROADMAP.md; docs/plans/2026-11-04-001-feat-daily-driver-roadmap-plan.md (this plan extends its U6-U15 and does not renumber them); docs/plans/2026-11-05-0806-feat-runtime-truth-and-dev-telemetry-plan.md (its U1-U7, PRs #117, #118, #120, #121, are the base for the live timeline here)
---

# Perplexity Computer parity - Plan

**Target repo:** open-computer. Paths are repo-relative and were read from `main` plus the in-flight PR file lists. Ids: **U** is plan-local (U1..U14), **P0-P3** is priority, **S/M/L** is effort. ROADMAP ids (OC-nnn) are cited where a unit lands one.

## Goal Capsule

- **Objective:** The owner's framing: "when I use Perplexity Computer it does X, so in Open Computer we do Y". This plan lists every user-visible Perplexity Computer behaviour, says what Open Computer does today, and names the unit that closes each gap, in an order the owner would feel.
- **Means:** map behaviours, not features. We copy what a user sees (plan first, live steps, a visible sandbox, per-step model and cost, check-ins, files out, schedules). We do not copy the cost structure: no premium default, no hosted credit economy.
- **Authority:** `ROADMAP.md` spend rule (no premium model as a default; ceiling Grok 4.7, orchestrator default `deepseek-v4-pro`, enforced by `tests/unit/test_spend_rule.py`), the approval gate, deny-by-default egress, the cost ledger, and the open-source hygiene test all stay exactly as strict as they are.
- **Execution profile:** code, one PR per unit, based on `main`, stacked only where "Depends on" says so. Docs-only PR for this file; it touches none of the files in #117, #118, #120, #121.
- **Stop conditions:** stop and ask before any unit that needs a live model call to prove it, widens egress, changes the approval gate, or adds a paid dependency. Do not merge; the owner merges.

---

## Product Contract

### Summary

Perplexity Computer is a cloud agent: you describe an outcome, an orchestrator splits it into subtasks, sub-agents run them in isolated sandboxes (filesystem, browser, tools), results land in a side panel and as files, work continues with the tab closed, and it can repeat on a schedule. Open Computer already has the hard, trust-bearing core (sandbox per subtask, egress policy, approval gate, cost ledger, durable schedules). What it lacks is the user-visible layer: the plan up front, streaming and a live timeline, anything that looks at the web or a browser, files and artifacts out, connectors, and a way to see spend and memory. This plan closes those in order.

### Perplexity Computer behaviour (researched 2026-11-05)

Sources are public and partly conflicting; items marked "unconfirmed" were not documented anywhere reachable.

| Behaviour | What the public sources say | Source |
|---|---|---|
| Task intake | Open the Computer icon, click "Start a task", describe an outcome. | [Perplexity launch post](https://www.perplexity.ai/hub/blog/introducing-perplexity-computer); [enterprise help mirror](https://perplexity-help-center.pages.dev/en/articles/13901210-computer-for-enterprise) |
| Clarifying questions | Not built in by default; a reviewer suggests a custom instruction to make it restate the outcome first. The launch post only says it will "check in if it truly needs you". Unconfirmed as a first-class UI. | [Aakash Gupta guide](https://www.news.aakashg.com/p/perplexity-computer-guide-product-managers); launch post |
| Plan up front | Orchestrator breaks the objective into subtasks and assigns them; a short plan before work starts is a user custom instruction, not a gate. | Aakash Gupta guide; launch post |
| Live step view and sandbox/browser view | Results appear in a right-hand panel; Comet iOS shows a panel running several steps. One review says there is no live preview of the agent's own sandbox or browser, so users cannot watch changes. Sources conflict. | Aakash Gupta guide; [Builder.io review](https://www.builder.io/blog/perplexity-computer); [PiunikaWeb](https://piunikaweb.com/2026/06/17/comet-ios-computer-tasks-any-page/) |
| Model choice per step | Routes subtasks across about 19 models without configuration (Opus-class core, Gemini for research, Grok for speed, GPT for long context). Users can assign a model per subtask; Effort Mode has Light, Standard, High, Ultra, plus Custom (model and reasoning level). | launch post; Builder.io; [Fello AI](https://felloai.com/perplexity-computer/) |
| Parallel sub-agents | Sub-agents run in parallel in isolated environments sharing a workspace; dozens of Computers can run at once; it can spawn agents to solve blockers. | launch post; Builder.io |
| Files and artifacts | Real filesystem; outputs are reports, slide decks, charts, data analysis, emailed deliverables, deployed sites. Built-in Skills: Slides, Research, Research Report, Chart. | Builder.io; Aakash Gupta guide |
| Connectors and integrations | 400+ managed OAuth connectors (Gmail, Slack, GitHub, Notion, Linear, Snowflake...), add your own MCP server URL; moving to a Plugins page bundling connectors and skills. Reviews report flaky connectors with no debug view. | Aakash Gupta guide; Fello AI; Builder.io |
| Credits and cost display | 100 credits = $1; per-account monthly spending cap (Max default $200, raisable); auto-refill off by default; tasks pause when credits run out and resume after top-up; costs are "examples, not guarantees" (15-70 credits for a quick fact, 15,000+ for an app). No per-step cost display documented; a reviewer reports a failed loop silently burning about 10,000 credits. | Fello AI; Builder.io |
| Pause, ask-user, approval | Scheduled tasks "can pause for your review before consequential actions"; users can require a check-in before executing. Unconfirmed UI details. | Fello AI; Aakash Gupta guide |
| Scheduled and recurring | Automations (added 2026-10-05): run on a schedule or when something happens in a connected app; runs whether or not the machine is on. | Fello AI; Aakash Gupta guide |
| Memory | Context persists across sessions, learns preferences; continuing a task is cheaper than starting new; compaction keeps long sessions coherent; no manual branch, clear or summarize. | Builder.io; Aakash Gupta guide |
| Sharing | Deployed outputs such as dashboards are shared by link. No task sharing documented. | Aakash Gupta guide |
| Also notable | Async: close the tab and it keeps working. Personal Computer variant reaches local Mac/Windows files and apps; a portable variant runs on the user's hardware without credits. Failures that are not surfaced cause runaway spend (Builder.io). | Fello AI; Builder.io |

### Requirements

- R1. A run shows a plan before it spends, and the owner can edit, approve, or cancel it. A model-asked clarifying question is answered in the thread, one at a time.
- R2. Every step is visible while it happens: streaming text, commands, and the sandbox's output; for browser steps, a view of what the browser sees.
- R3. Every step names the model that actually served it and its real cost, and the run shows spend against its cap; the cap pauses, never silently drops, work.
- R4. The model can search the web, fetch pages, and drive a browser, all through the existing egress policy and approval gate.
- R5. Independent subtasks run in parallel with carved budgets; the owner can choose a model per step.
- R6. Outputs are first-class: files and artifacts in the thread workspace, listed on an Artifacts page, exportable, shareable by explicit owner action.
- R7. Connectors are MCP servers the owner adds; each shows health and what it can do; credentials never enter a sandbox (OC-032/033).
- R8. Work runs without the tab open, can be scheduled or event-triggered, and consequential actions in an unattended run wait for approval.
- R9. A persistent, inspectable, owner-editable memory feeds new threads; nothing is remembered silently.
- R10. No test makes a live model call; every model, search, browser and connector boundary is an injected fake.

### Scope boundaries

- Not copied: a hosted credit economy, a 400-connector marketplace, a premium default model, a hosted multi-user mode, local-desktop control of the owner's machine (the device plane in `services/device*` is the separate path for that).
- Not changed: approval gate, egress deny-by-default, ledger hard cap semantics, spend rule.

### Deferred to follow-up work

- Event-triggered automations beyond cron and webhook (U12 starts with cron plus a webhook trigger).
- Sharing beyond an owner-created read-only link (U8 stops at export).
- Temporal-backed durable threads (OC-078), unchanged from the daily-driver plan.

---

## Planning Contract

### Parity table

"Today" cites what is on `main` unless it names a PR. PRs #117, #118, #120, #121 are the runtime-truth/dev-telemetry series; the **model-fallback PR (in flight)** swaps the default orchestrator to `particle-ai/particle-1`, adds an OpenRouter `models`-array fallback chain on every chat call and records the actually-serving model.

| # | Perplexity Computer does | Open Computer today | We build | Unit | Pri | Eff |
|---|---|---|---|---|---|---|
| 1 | Takes an outcome in one box | Home composer and chat-first threads: `ui/src/components/Composer.tsx`, `ui/src/components/ThreadView.tsx`, `services/opsview/threads.py`, `services/orchestrator/chat.py` (decides reply, ask or run) | Keep. Gap is the mode switch (Search or Computer), covered by the daily-driver U11 | none here | - | - |
| 2 | Asks clarifying questions only when needed | `chat.py` supports `{"action":"ask"}` (one short question) in a thread, but only as free text; no structured answer chips, nothing when a goal is submitted outside a thread | Structured `ask` card with suggested answers, shown in the thread and on goal submit; one question at a time | U2 | P1 | S |
| 3 | Shows a plan before working | `services/orchestrator/decompose.py` builds the DAG, but the plan is only visible as steps appear on the run page; no preview, edit or approve before spend (`services/opsview/goalruns.py`) | Plan preview card (steps, deps, model and cost estimate per step) that waits for owner approval when the run is above a threshold or the owner sets "review plan first"; edits re-validate through `decompose._canon` | U1 | P0 | M |
| 4 | Live step panel as work happens | No streaming; thread view polls (`ThreadView.tsx`). #118 adds the per-run event stream (`oc/events/log.py`, events API), #121 adds a dev-mode timeline (`ui/src/components/runtime/Timeline.tsx`), both open and dev-mode gated; daily-driver U2 plans SSE streaming | Promote the timeline from dev mode to the default run view once #118 and #121 merge, add live status words and stuck-step reasons; reuse daily-driver U2 SSE rather than a second channel | U3 | P0 | M |
| 5 | Isolated sandbox with a real filesystem | Strong already: sandbox per subtask via `services/execplane/` (CubeSandbox adapter, `space.py`), default-deny egress (`egress_defaults.py`, `policy.py`), fsproto isolation; #117 adds boot retry with backoff | Keep. Add a read-only file browser for the run's workspace (list, preview) | U8 | P1 | S |
| 6 | A view of the sandbox or browser the agent is using | Nothing user-visible; the run page shows command output text only. No browser worker (OC-045, OC-083 open) | Per-step command and file-change feed first (from #118 events), then browser screenshots streamed per action as events | U3, U6 | P0/P1 | M |
| 7 | Searches the web and fetches pages | No web tool. Workers act through `services/orchestrator/engines.py` on sandbox commands; egress must be pre-approved; OC-075 open | `web_search` and `web_fetch` tools executed host-side through an injected provider, results into the thread, sources cited in the answer | U5 | P0 | M |
| 8 | Drives a real browser to do things | No browser tool; `services/device_agent/wisp_actuator.py` is the device plane, not a sandbox browser | Browser worker in a sandbox (OC-045) behind egress policy and the gate, with screenshot events; text-first snapshots like the agent browsers the owner already uses | U6 | P1 | L |
| 9 | Routes each subtask to the best of ~19 models | Router decides tier per step (`services/router/decide.py`, `policy_table.py`), curated catalog `services/router/model_catalog.json` (5 models, `tools` flag, spend ceiling), effort presets `services/opsview/effort.py`; #120 shows worker model apart from router decision and the fallback cause; model-fallback PR (in flight) records the serving model | Show model per step by default (consumes #120 and the fallback PR, no new storage); widen the catalog by owner decision only | U4 | P1 | S |
| 10 | Lets the user pick a model per step or an effort level | Effort composer Low/Medium/High with budget clamp (`effort.py`, `EffortPicker.tsx`); no per-step override | Per-step model override in the plan preview (U1), limited to catalog entries under the ceiling; Effort stays the default entry | U1 | P2 | S |
| 11 | Runs sub-agents in parallel with a shared workspace | DAG of subtasks with leases (`services/orchestrator/dispatch.py`, `oc/fsproto`); worker concurrency is bounded by the dispatch loop; no subagent tool inside a turn (OC-076 open); shared workspace per run only via task dependency results | Subagent tool: a turn spawns N child goals with carved budgets from the parent cap; parent waits and synthesises; per-run shared scratch dir | U7 | P1 | L |
| 12 | Produces files, decks, reports, charts | Run output text only; `services/workspace/` has artifact fixtures and a replay UI (fixtures, `routes.py`) but no live artifact write path; Artifacts page is OC-081 (open) | `write_file` and `publish_artifact` tools into the thread workspace; Artifacts page; export as zip or single file; a small set of built-in skills (report, slides outline, chart) as markdown skills | U8 | P0 | M |
| 13 | Connectors (OAuth) and bring-your-own MCP | `services/mcp/server.py` is an MCP **server** (stdio, 8 tools); no MCP client, no Plugins page; `ui/src/pages/Skills.tsx` shows capability health only (OC-082 open) | MCP client with stdio servers first, Plugins page with per-connector health and a test button, credentials via host-side substitution (OC-032/033), approval on first use of each tool | U9 | P1 | L |
| 14 | Shows cost in credits, has a spend cap, pauses at the limit | Strong core: `services/cost/ledger.py` per-run cap that pauses, never cancels, resume; `OC_DAILY_CAP`; BYOK cost under-reporting fixed by runtime-truth plan U2; #120 shows real cost per step; Usage page is daily-driver U12 (OC-091) | Show spend against the cap live in the thread header and on every step; "paused at cap, resume with a new cap" card; weekly and daily totals on Usage. No credits, dollars only | U4 | P0 | S |
| 15 | Pauses for review before consequential actions | Approval gate (`services/gate/`), hash-bound single-use approvals, console page `ui/src/pages/Approvals.tsx`; daily-driver U4 moves it inline | Consume daily-driver U4; add an unattended-run policy: approval timeouts park the run, never auto-approve (U12) | U12 | P1 | S |
| 16 | Scheduled and recurring tasks, event-triggered | Cron-style schedules via Temporal: `services/opsview/automations.py` (`every_minutes`, pause, trigger, delete), `ui/src/pages/Automations.tsx`; automations post to goal runs not threads (OC-084 open); no event triggers | Automations post into a thread, run summary on completion, webhook trigger, failure notification; retire the goal-run DAG duplication per OC-084 | U12 | P2 | M |
| 17 | Keeps working with the tab closed | Runs are server-side (`goalruns.py`, durable workers `services/durable/`); a turn is a daemon thread and is lost on restart (daily-driver U1) | Consume daily-driver U1; add a "while you were away" summary on reopening | U10 | P2 | S |
| 18 | Memory across sessions and compaction | No cross-thread memory; compaction is daily-driver U14 (OC-077); `docs/plans/2026-10-31-003-feat-chat-agent-product-plan.md` has no memory unit | Owner-visible memory file(s): proposed facts appear as "remember this?" cards, stored as plain markdown in the data dir, injected into new threads, editable and deletable on Settings | U11 | P2 | M |
| 19 | Share outputs by link | Nothing | Owner-created read-only export (static HTML or zip) of one artifact or one thread, no hosted service, no public URL by default; hosted link only after the Cloudflare frontend decision (OC-092) | U13 | P3 | M |
| 20 | Spawns agents to unblock itself, fixes its own failures | Planner retries on enum near-miss and sandbox boot are fixed by the runtime-truth series (#117 and its siblings); no general self-repair loop | Out of scope here; revisit after U7 | none | - | - |
| 21 | Mobile and other surfaces | Responsive console; `oc` CLI chat is OC-085; iOS planning exists separately | No new unit | none | - | - |

### Top 5 gaps

Ordered by how much of the product the owner would not recognise as "Computer" without it.

1. **No plan up front (rows 3, 10, 2).** A goal spends money the moment it is submitted; Perplexity-style review of the plan, its models and its cost is missing. U1, U2.
2. **No live view of the work (rows 4, 6).** The thread polls and the timeline is dev-mode only behind #118 and #121; nothing shows what a sandbox or browser is doing. U3.
3. **No web or browser capability (rows 7, 8).** The model cannot look anything up or drive a page; that is most of what people ask a computer for. U5, U6.
4. **No outputs as files (rows 12, 5).** Results are text on a run page; no artifacts, no Artifacts page, no export. U8.
5. **No connectors or subagents (rows 13, 11).** No MCP client or Plugins page, and no parallel subagent tool with carved budgets. U9, U7.

Memory, scheduling polish and sharing (rows 16-19) are real gaps but lower value; they are P2/P3.

### Key technical decisions

- KTD1. Build on the event stream, do not add another. The plan preview, live panel, browser screenshots and subagent progress are all event types on the per-run stream from #118 (`oc/events/log.py`, `docs/design/event-stream.md`); daily-driver SSE (U2 there) carries them to the UI. Reason: three series already want a stream; a fourth would diverge.
- KTD2. Plan review is a state, not a mode. A run has `planned` then `approved`; auto-approve below an owner-set cost estimate keeps the one-shot flow, so the common case does not get slower. Reason: Perplexity does not gate on a plan; making it mandatory would regress the daily driver.
- KTD3. Estimates are ranges from catalog prices and recorded token history, labelled estimates, never guarantees (Perplexity makes the same disclaimer). The cap remains the only hard number.
- KTD4. Web and browser tools run on the host side through the egress policy's approved list, not by opening sandbox egress. Fetched content is untrusted data, wrapped and PII-prefiltered before it reaches a model, and prompt-injection text in it never changes a tool permission.
- KTD5. Subagent budgets are carved from the parent's cap and returned unspent; the parent cannot exceed its own cap through children. A child cannot approve anything.
- KTD6. Memory is plain markdown on disk, proposed by the model and accepted by the owner. Nothing writes to it without a visible card.
- KTD7. No premium default and no Opus-class routing for parity's sake. Model choice stays inside the catalog ceiling; widening it is an owner decision (D1).
- KTD8. This plan does not renumber daily-driver or runtime-truth units. Where a unit there already covers a behaviour, the parity table says "consume".

### Sequencing

1. **Wave A, feel and trust (P0):** U4 (cost and model on every step; consumes #120 and the model-fallback PR) -> U3 (live panel; needs #118 and #121 merged) -> U1 (plan preview) with U2. U5 (web tools) and U8 (files and artifacts) can run in parallel with U1 since they touch the tool layer, not the run page; both depend on daily-driver U6 (native tool calling).
2. **Wave B, capability (P1):** U7 (subagents), U9 (MCP client and Plugins), U6 (browser worker).
3. **Wave C, ambient (P2/P3):** U10, U11, U12, U13.

U3 and U4 edit `RunView.tsx` and `api.ts`, which #117, #120, #121 also edit; they wait until those merge rather than stacking on them.

### Assumptions

- #109-#111 merge first, then the runtime-truth series, then daily-driver U1-U6, in the order the owner chooses; paths below that come from open PRs are confirmed against `main` when a unit starts.
- The model-fallback PR (in flight) lands the `models`-array fallback and per-call served-model record in `services/openrouter/client.py`; U4 reads that record rather than adding its own.
- OpenRouter tool calling follows the wire format already in `services/openrouter/client.py`.

### Risks

| Risk | Mitigation |
|---|---|
| Web fetch brings prompt injection into the orchestrator | KTD4; tool results are tagged untrusted, tools never gain permissions from content; adversarial fixture test |
| Browser worker widens egress | Same approved-host list; first navigation to a new host is an approval; screenshots are PII-scanned before display |
| Plan preview slows every task | KTD2 auto-approve under a threshold |
| Subagents multiply spend | KTD5; per-child cap; parent pauses when the pool is empty |
| Connector credentials leak into a sandbox | Host-side substitution only (OC-032/033); test that no secret string appears in any sandbox env or file |
| Scope overlap with daily-driver units | KTD8; each unit lists "consumes" |

---

## Implementation Units

| U-ID | Title | Pri | Eff | Key files | Depends on |
|---|---|---|---|---|---|
| U1 | Plan preview, edit and approve before spend | P0 | M | `services/orchestrator/decompose.py`, `services/opsview/goalruns.py`, `ui/src/components/PlanCard.tsx` | none (U4 for estimates) |
| U2 | Structured clarifying question with answer chips | P1 | S | `services/orchestrator/chat.py`, `ui/src/components/ThreadView.tsx` | daily-driver U6 for typed `ask` |
| U3 | Live run panel as the default view | P0 | M | `ui/src/components/runtime/`, `ui/src/components/RunView.tsx`, `services/opsview/server.py` | #118, #121 merged; daily-driver U2 |
| U4 | Model and cost on every step, spend against cap | P0 | S | `services/cost/ledger.py`, `ui/src/components/RunView.tsx`, `ui/src/lib/format.ts` | #120, model-fallback PR |
| U5 | Web search and fetch tools | P0 | M | `services/orchestrator/tools/web.py` | daily-driver U6 |
| U6 | Sandbox browser worker with screenshot events | P1 | L | `services/orchestrator/tools/browser.py`, `services/execplane/` | U5, U3 |
| U7 | Parallel subagents with carved budgets | P1 | L | `services/orchestrator/subagent.py`, `services/cost/ledger.py` | daily-driver U6, U5, U8 |
| U8 | Files, artifacts, Artifacts page, run workspace browser, export | P0 | M | `services/orchestrator/tools/files.py`, `ui/src/pages/Artifacts.tsx` | daily-driver U6, U7 shell |
| U9 | MCP client and Plugins page | P1 | L | `services/mcpclient/`, `ui/src/pages/Plugins.tsx` | U8 |
| U10 | While-you-were-away summary | P2 | S | `services/opsview/threads.py`, `ui/src/components/ThreadView.tsx` | daily-driver U1 |
| U11 | Owner-visible memory | P2 | M | `services/orchestrator/memory.py`, `ui/src/pages/Settings.tsx` | daily-driver U12 |
| U12 | Automations into threads, webhook trigger, unattended approval policy | P2 | M | `services/opsview/automations.py`, `ui/src/pages/Automations.tsx` | daily-driver U4, U15 |
| U13 | Owner-created read-only export | P3 | M | `services/workspace/`, `ui/src/pages/Artifacts.tsx` | U8 |
| U14 | Parity re-check and golden demo run | P2 | S | `docs/FEATURES.md`, `tests/e2e/` | all above |

### U1. Plan preview, edit and approve before spend

- **Goal:** The owner sees what will run, on which model, at what estimated cost, before money is spent. (R1)
- **Approach:** After `decompose`, store the plan on the run with state `planned`. If the estimate is under the owner's auto-approve threshold (default on, so one-shot flow is unchanged) go straight to `approved`; otherwise surface a plan card in the thread and the run page. Edits (remove, reorder, change a step's model within the catalog) go back through `decompose._canon` and `_topo` validation. Estimate per KTD3.
- **Test scenarios:** under-threshold plan auto-approves; over-threshold waits and a rejection cancels with nothing spent; an edit that creates a cycle is refused; a model above the ceiling is refused; restart while `planned` re-shows the card.
- **Negative control:** with validation off, the cycle test fails.
- **Verification:** `uv run --locked pytest tests/opsview/test_goals.py tests/unit/test_decompose.py`; UI e2e with light and dark screenshots.

### U2. Structured clarifying question

- **Goal:** One question at a time, answered with a tap or free text. (R1)
- **Approach:** Extend the `ask` decision with optional `choices`; render as chips in the thread; the answer is appended as a user message. Models without tool support keep the JSON fallback (KTD5 of daily-driver).
- **Test scenarios:** choices parse and render; free-text answer works; a second question before the first is answered is refused; unknown fields are dropped.
- **Verification:** pytest for chat parsing; e2e.

### U3. Live run panel as the default view

- **Goal:** Watch the work happen, not just its result. (R2)
- **Approach:** Take the #121 timeline out of dev mode for the owner-facing subset (step state, streamed text, command and output, wait reasons); keep raw model-call detail behind dev mode. Subscribe over daily-driver SSE; fall back to polling.
- **Test scenarios:** event order matches poll transcript; a stuck step shows what it waits on; dev mode off shows no raw prompts.
- **Verification:** pytest on the events API; e2e screenshots.

### U4. Model and cost on every step, spend against cap

- **Goal:** No step is anonymous and spend is never a surprise. (R3)
- **Approach:** Read the served model and real cost already recorded by #120 and the model-fallback PR; add a thread-header spend bar (spent over cap) and a "paused at cap" card with resume. No new storage.
- **Test scenarios:** BYOK cost counts toward the cap; a fallback-served step shows the served model, not the requested one; cap pause shows resume and resume continues.
- **Verification:** `tests/unit/test_worker_truth.py`-style fakes plus UI e2e.

### U5. Web search and fetch tools

- **Goal:** The model can look things up. (R4)
- **Approach:** Host-side `web_search` and `web_fetch` with an injected provider and fetcher; allowed hosts follow the egress list; content tagged untrusted and size-capped; citations carried into the answer. Provider choice is D2.
- **Test scenarios:** search returns cited results with a fake provider; fetch to a non-allowed host is refused; a page containing instructions does not change tool permissions; oversize page is truncated.
- **Verification:** pytest; no network in tests.

### U6. Sandbox browser worker

- **Goal:** Do things on pages. (R2, R4)
- **Approach:** Builds OC-045: a browser inside the sandbox driven by the worker, snapshot-text first, screenshots emitted as events (U3 renders them). Navigation to a new host goes through the gate.
- **Test scenarios:** fake browser backend scripts a page flow; screenshot event arrives per action; new-host navigation requires approval; a PII-bearing screenshot is flagged.
- **Verification:** pytest with fake backend; live check is an owner-run step, not CI.

### U7. Parallel subagents with carved budgets

- **Goal:** Independent work runs side by side. (R5)
- **Approach:** `spawn_subagent` tool creates child goals with a slice of the parent cap (KTD5); shared scratch dir per run; parent receives results as tool output. Concurrency limit is a setting.
- **Test scenarios:** children cannot exceed the parent's remaining cap; unspent budget returns; a child's approval request is routed to the owner and the child cannot self-approve; parent stop cancels children.
- **Verification:** pytest with fake engine; ledger invariant test with a mutant.

### U8. Files, artifacts and export

- **Goal:** Results are things you can open and keep. (R6)
- **Approach:** `write_file`, `publish_artifact` in the thread workspace; Artifacts page lists them with preview; a read-only run workspace browser; export as file or zip. Reuse `services/workspace/` preview code where it fits.
- **Test scenarios:** path traversal refused; large file refused with a clear note; artifact survives restart; export contains exactly the listed files.
- **Verification:** pytest and e2e; hygiene test.

### U9. MCP client and Plugins page

- **Goal:** Bring your own tools. (R7)
- **Approach:** stdio MCP client first, Plugins page lists servers, tools and health with a test button; first use of a tool needs approval; secrets substituted host-side only.
- **Test scenarios:** a fake server's tools appear; a hung server times out and shows unhealthy; no secret string appears in sandbox env; tool approval is per tool and hash-bound.
- **Verification:** pytest; conformance R3-style secret check.

### U10-U14 (short form)

- **U10 While you were away:** on reopening a thread, one summary message of what finished, failed or waits. Tests: summary covers a run that finished during downtime.
- **U11 Memory:** proposed facts as cards, accepted ones written to a markdown file, injected as a bounded preamble, deletable on Settings. Tests: nothing is written without acceptance; deleted fact never reappears in the next prompt.
- **U12 Automations into threads:** a fire posts to a thread, notifies on failure, webhook trigger with a shared secret; unattended approval timeouts park the run. Tests: fire creates one thread message; approval timeout never auto-approves.
- **U13 Export:** static read-only bundle of an artifact or thread, no hosted URL. Tests: bundle contains no absolute paths or secrets.
- **U14 Parity re-check:** rerun this table against `main`, record the result in `docs/FEATURES.md`, and one fake-engine golden run that exercises plan, stream, tool, artifact.

---

## Open owner decisions

- **D1. Model ceiling.** Perplexity's quality comes partly from an Opus-class core. Keep the Grok 4.7 ceiling and `particle-ai/particle-1` or deepseek defaults (recommended), or allow an opt-in premium tier per run? Affects U1 and U4 only through the catalog.
- **D2. Web search provider.** Which provider, and is a paid key acceptable? Recommended: a keyless or low-cost provider behind the injected interface, decision before U5.
- **D3. Plan review default.** Auto-approve threshold default (recommended: on, at the effort preset's budget) or always review?
- **D4. Browser worker.** Build OC-045 in-sandbox (recommended), or reuse the owner's existing agent browser through the device plane?
- **D5. Connector scope.** MCP-only (recommended) or also managed OAuth for a few services (Gmail, GitHub, Slack)? The latter needs a credential store decision tied to OC-033.
- **D6. Memory scope.** Global only, or per project once Projects exist (OC-090)?
- **D7. Sharing.** Is export-only enough, or is a hosted link wanted (needs OC-092 and a threat model)?

## Scope check against in-flight work

This plan adds no code and no edits to ROADMAP.md. It does not touch any file in #117, #118, #120 or #121. Units that overlap their files (U3, U4) are sequenced after them and consume their output.
