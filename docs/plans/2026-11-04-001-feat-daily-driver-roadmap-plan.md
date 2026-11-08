---
title: Daily-driver roadmap completion - Plan
type: feat
date: 2026-11-04
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
depth: deep
related: docs/plans/2026-10-31-003-feat-chat-agent-product-plan.md (its decisions KTD1-KTD17 still hold; this plan re-sequences what remains from today); docs/plans/2026-11-01-001-feat-cloudflare-hosted-frontend-plan.md
---

# Daily-driver roadmap completion - Plan

**Target repo:** open-computer. All paths are repo-relative. Ids continue the ROADMAP (OC-068..OC-092 for the chat product, OC-015..OC-042 for test hardening). Plan-local ids are R (requirement), KTD (decision), U (unit).

**Base assumption:** PRs #109 (delete, archive, clear runs), #110 (chat-first threads) and #111 (run page PII label, OC-069) merge to `main` first, in that order. Paths below that come from #110 (`services/opsview/threads.py`, `services/orchestrator/chat.py`, `ui/src/components/ThreadView.tsx`, `ui/src/components/Sidebar.tsx`) are the shape on that branch; confirm them against `main` when a unit starts.

## Goal Capsule

- **Objective:** The owner opens Open Computer, types, and gets a conversation that works: replies appear as they are written, work happens inline and can be stopped or steered, approvals are answered in place, and nothing needs cleaning up afterwards. It is the thing they reach for daily instead of a chat app plus a terminal.
- **Means:** extend the thread product #110 started, in small PRs ordered by what the owner feels first (KTD1).
- **Authority:** the Product Contract below, then `ROADMAP.md`, then `docs/plans/2026-10-31-003-feat-chat-agent-product-plan.md`. The approval gate, deny-by-default egress, cost ledger, spend rule and open-source hygiene test stay exactly as strict as they are.
- **Execution profile:** code, one PR per unit (per row in U16 and U17), each reviewable alone, stacked only where the "Depends on" column says so, retargeted to `main` before the parent merges.
- **Stop conditions:** stop and ask before any unit that needs a live model call to prove it, changes the approval gate, widens egress, or adds a paid dependency. Do not merge; the owner merges.

---

## Product Contract

### Summary

#109-#111 removed the biggest friction: one box, a model that decides reply, ask or run, run output the model can read, and a way to delete junk. A thread still feels slow and brittle: the reply arrives all at once after polling, a turn started just before a console restart gets no answer, there is no way to interrupt or redirect a turn, approvals live on another page, and the left navigation is still the flat operator list. This plan finishes the roadmap in the order the owner would notice, ending with the long-horizon and ecosystem work, and weaves the P1/P2 test-hardening rows in as a trailing lane so safety paths are not left behind.

### Problem frame

Owner verdict (2026-11-04): still unusable, too much in the way of just chatting and having it work on things; it one-shots and leaves junk chats. Open gaps against `main` plus the three open PRs:

| # | Gap | Why it blocks daily use |
|---|---|---|
| G1 | A turn is a daemon thread; nothing resumes it after a restart | A message sent shortly before a restart is lost silently, which destroys trust |
| G2 | No streaming; the thread view polls and shows the full answer at once | Feels dead for 5-30 s on every message |
| G3 | No interrupt, no steer, one turn at a time | Cannot correct a wrong direction without waiting it out |
| G4 | Approvals are on a separate page | A run that needs approval looks hung inside the thread |
| G5 | The orchestrator speaks a hand-parsed JSON decision, not native tool calls (OC-071, OC-073) | Fragile on weaker models, no parallel or typed tools, no web or file tools |
| G6 | No routing truth beyond the PII label: the thread does not say which model served a turn (OC-068) | Owner cannot trust cost or privacy claims |
| G7 | Flat operator navigation, no Computer, Automations, Artifacts, Plugins groups; operator pages not behind a Developer area (OC-072, OC-089) | "Too much crap" before chat |
| G8 | No web search, no files or artifacts, no subagents (OC-074..076) | The model cannot do the work people ask a computer to do |
| G9 | No Settings, Artifacts, Plugins or Usage pages (OC-080..082, OC-091) | Cannot see or control spend, outputs, connectors |
| G10 | No compaction or durable threads (OC-077, OC-078) | Long threads degrade or die |
| G11 | P1/P2 rows open (the subset scheduled in U16 and U17) | Safety paths lack golden, mutation, chaos and audit-chain proof |

### Requirements

- R1. A message the owner has sent is never silently lost: after a console restart, every thread whose last turn did not finish is resumed or marked interrupted with a visible, one-click retry. (G1)
- R2. Assistant text streams into the thread as it is produced, with a polling fallback that yields the same final transcript. (G2)
- R3. The owner can stop a turn (and the run it started) in one action, and can send a message while a turn is working; it is delivered at the next safe point. (G3)
- R4. When a run in a thread needs approval, the thread shows an inline card with who, what, where, cost, PII and hash (the fields #96 already shows), and approve or reject there, with the same hash-bound single-use gate as the Approvals page. (G4)
- R5. The orchestrator uses native OpenRouter tool calling with typed tools; the JSON-decision parser stays as the fallback for models without tool support. Runs stay bounded per message. (G5)
- R6. Every assistant turn and every run step records and shows the model that actually served it and its cost; local models are opt-in; PII-held steps wait for approval. (G6)
- R7. The default navigation is New, Computer, Automations, Artifacts, Plugins, then Pinned, Projects, Recents; every operator page moves under one Developer entry. The app opens on the composer. (G7)
- R8. The model can search the web, fetch a page, read and write files in the thread workspace, publish artifacts, and ask the owner, all gated by the existing egress policy. It can spawn subagents with carved budgets. (G8)
- R9. Settings, Artifacts, Plugins and Usage pages exist and are small, with a few visible actions each. (G9)
- R10. Long threads compact without losing decisions (U14); a thread survives a console restart through the U1 resume. The durable engine (OC-078) is deferred. (G10)
- R11. The P1/P2 safety rows listed in U16 and U17 get tests that fail against a broken fake. Other open rows (OC-019, 020, 022, 023, 027, 038) stay on the ROADMAP and are not scheduled here; OC-039 is absorbed by OC-085 in U15. (G11)
- R12. Junk never accumulates: empty and failed-at-start threads do not appear in Recents, retention is explicit and visible in Settings, and bulk clear stays one action.
- R13. No test makes a live model call; every model, search and sandbox boundary is an injected fake.
- R14. Every UI change follows the polish bar (KTD6) and ships light and dark screenshots.

### Scope boundaries

- Not built here: the round voice-mode button, a hosted multi-user mode, payments, the Anthropic directory listing (OC-064/065), plugin marketplace packaging for other IDEs (OC-060..063).
- OC-092 (Cloudflare-hosted frontend) stays awaiting the owner's go-ahead and is sequenced as an optional last unit; it does not block the daily driver.

### Deferred to follow-up work

- Temporal-backed durable threads (OC-078) beyond the file-based resume in U1; revisit when a thread must outlive a host reboot mid-run.
- Browser tool (OC-083) and the Plugins page connector catalog beyond MCP stdio servers.

---

## Planning Contract

### Key technical decisions

- KTD1. Order by user-visible value, not by architecture layer. Each unit must change what the owner sees in one sitting, or be a pure safety-net unit placed in the trailing lane. Rationale: the owner is trying to use the product daily; layers that show nothing (store rewrites, engine swaps) wait until a visible unit needs them.
- KTD2. Thread storage stays one JSON file per thread through U14. A thread gains a `turn_counter`, a nullable `active_turn` marker (set while a turn is in flight) and per-message `seq`, written atomically (temp file plus rename, already the pattern in `services/opsview/threads.py`). Moving to SQLite or the durable engine is a separate decision at U15 with a migration test, not a side effect of an earlier unit.
- KTD3. Streaming is Server-Sent Events from the existing stdlib HTTP server, following the SSE helper in `services/workspace/routes.py`. Event types: `message.delta`, `message.done`, `run.state`, `approval.requested`, `turn.done`, `turn.error`; an interrupted turn is a `turn.done` with `reason: interrupted`. Every event carries `seq`; the client resumes with `Last-Event-ID`. Polling `GET /api/threads/<id>` remains and returns the same transcript, so the SPA degrades to polling if the stream fails.
- KTD4. One turn loop owns cancellation. A turn has a `threading.Event` cancel token checked between model chunks and while waiting on a run; cancel calls the existing run cancel path and records an `interrupted` message. Steer is a queue: a user message sent during a working turn is appended and the loop reads it at the next decision point, never mid-token.
- KTD5. Native tool calling is additive. `services/orchestrator/chat.py` keeps `parse_decision` for models that lack `tools`; a catalog flag `supports_tools` (added to the catalog schema and loader validation in `services/router/model_catalog.*` with a loader test, then exposed by `GET /api/models`, OC-087) selects the path. The `run` tool still creates a goal run through `goalruns`, so approvals, ledger and caps are unchanged. `MAX_PASSES` becomes a per-message run budget plus the existing per-run dollar cap.
- KTD6. Polish bar for every UI unit: design tokens only (`silver-lining.css` and `oc-scale.css` variables; no raw hex, px colour or ad hoc shadow in components); one accent per screen, and it means "live or needs you"; few visible actions, the rest in an overflow menu or on hover-reveal rows; hover state is instant (no transition delay on hover colour or background); motion uses springs, with `prefers-reduced-motion` honored; light and dark screenshots at 1440 and 390 wide attached to the PR, taken with `tests/e2e/shots.mjs`; axe reports 0 violations.
- KTD7. Tests never call a live model. The model engine, the web search provider, the web fetcher and the sandbox launcher are injected callables (the `Deps` pattern in `services/opsview/threads.py`); streaming tests feed scripted chunk lists; SSE tests read from a loopback server with a fake engine.
- KTD8. Open-source hygiene: fixtures and screenshots use neutral names ("Owner", "Example thread"); no machine names, domains, paths or personal data in code, tests, fixtures, docs or PR text. `tests/unit/test_obs_open_source_hygiene.py` must pass on every unit.

### High-level technical design

This illustrates intent and is directional, not implementation.

```text
SPA ThreadView ---POST /api/threads/<id>/messages---> thread store (append, seq++)
      ^                                                    |
      | SSE: message.delta / run.state /                   v
      | approval.requested / turn.done            turn loop (cancel token, steer queue)
      +---------------------------------------------------+
                                                   |  model engine (streaming, tools)
                                                   +--> tools: run, ask, web, files, artifact, subagent
                                                   |         |
                                                   |         +--> goalruns -> gate -> approvals (inline card)
                                                   +--> ledger (served model + cost per turn and step)
startup: scan threads where last turn unfinished -> resume or mark interrupted (U1)
```

### Sequencing

The order of the units is the order of value. Units that edit the same files (U1-U3 and U5 on the thread store and client; U7 and U8 on the sidebar) are stacked, not parallel. U1-U5 make one thread feel right and are the gate for calling it a daily driver. U6-U8 remove clutter and add real work. U9-U13 add pages and capabilities. U14-U15 are long-horizon. U16-U17 are the safety-net lane and may be picked up by a parallel worker at any point (they have no dependencies), since they touch tests and safety modules, not the thread UI.

### Assumptions

- #109-#111 merge as described in the status note; if #110 changes shape in review, U1-U3 adjust file paths only.
- OpenRouter streaming and tool calling follow the OpenAI-compatible wire format already used in `services/openrouter/client.py`.
- Catalog entries can carry a `supports_tools` flag; U6 adds it to the loader's validation and tests, leaving the spend-rule logic itself unchanged.

---

## Implementation Units

| U-ID | Title | Key files | Depends on |
|---|---|---|---|
| U1 | Turn durability: resume or mark interrupted after restart | `services/opsview/threads.py`, `services/opsview/server.py` | none |
| U2 | Streaming replies (client, SSE, thread view) | `services/openrouter/client.py`, `services/opsview/server.py`, `ui/src/components/ThreadView.tsx` | U1 |
| U3 | Stop and steer | `services/opsview/threads.py`, `ui/src/components/ThreadView.tsx` | U2 |
| U4 | Inline approval card | `ui/src/components/ThreadView.tsx`, `services/opsview/server.py` | U2 |
| U5 | Routing truth shown in the thread (OC-068) | `services/placement/`, `services/orchestrator/engines.py`, `services/openrouter/client.py`, `ui/src/components/RunView.tsx`, `ui/src/components/ThreadView.tsx` | U2 (shares the client and ThreadView) |
| U6 | Native tool calling and the typed `run`/`ask` tools (OC-071, OC-073) | `services/orchestrator/chat.py`, `services/openrouter/client.py` | U2, U3 |
| U7 | Shell: grouped navigation, operator pages under Developer, open on the composer (OC-072, OC-089) | `ui/src/components/Sidebar.tsx`, `ui/src/App.tsx` | none |
| U8 | Junk control and thread hygiene: auto-title, hide empty and failed-at-start, pins, search, retention (OC-090 part) | `services/opsview/threads.py`, `ui/src/components/Sidebar.tsx` | U7 |
| U9 | Web search and fetch tools (OC-075) | `services/orchestrator/tools/web.py` | U6 |
| U10 | Files and artifacts tools, Artifacts page (OC-074, OC-081) | `services/orchestrator/tools/files.py`, `ui/src/pages/Artifacts.tsx` | U6, U7 |
| U11 | Home composer and pickers: Search or Computer mode, model and reasoning chip, retire the effort picker (OC-088, OC-087 pickers) | `ui/src/components/Composer.tsx`, `ui/src/components/EffortPicker.tsx` | U6, U7, U9 |
| U12 | Usage and Settings pages (OC-091, OC-080) | `ui/src/pages/Settings.tsx`, `services/opsview/server.py` | U5, U7, U8 |
| U13 | Subagents with carved budgets (OC-076) | `services/orchestrator/subagent.py` | U6, U9, U10 |
| U14 | Context compaction (OC-077) | `services/orchestrator/context.py` | U6 |
| U15 | MCP client and Plugins page; automations post to threads, retire the goal-run DAG, `oc` chat CLI (OC-082, OC-084, OC-085) | `services/mcpclient/`, `oc/cli/` | U10, U12, U13 |
| U16 | Safety-net lane A: audit chain, policy fixture, ops view and approval console goldens (OC-016, OC-021, OC-025, OC-026) | `services/gate/`, `tests/unit/`, `tests/opsview/` | none |
| U17 | Safety-net lane B: router golden replay, budget tests, chaos suite, coverage gate (OC-017, OC-018, OC-024, OC-028, OC-037) | `services/router/`, `tests/unit/`, `tests/conformance/` | none |

Optional last: OC-092 Cloudflare-hosted frontend per its own plan, once the owner approves. OC-078 durable threads: see Deferred.

### U1. Turn durability: resume or mark interrupted after restart

- **Goal:** A message sent just before a restart gets an answer or an honest, retryable state. (R1)
- **Requirements:** R1, R12
- **Files:** `services/opsview/threads.py`, `services/opsview/server.py` (startup hook), `tests/opsview/test_threads.py`
- **Approach:** Set `active_turn` on a thread when a turn starts and clear it when the turn writes its final message. On server start, scan threads: a thread with a set `active_turn` and no live worker is either resumed (last message is a user message or a finished run, so the next model call is safe and idempotent) or, if a run was mid-flight, re-attached to that run's state through `goalruns`. Anything unsafe to resume gets an `interrupted` message with a Retry action. Resume is bounded to once per thread per boot to avoid loops. Idempotency: the turn loop derives a deterministic run id from the thread, turn counter and pass number and writes it to the thread before starting the run, so a crash between start and record cannot orphan a second run; a resumed pass finds the existing run instead of starting another. A last message that is assistant text with a `run` decision but no recorded run is resumed from that decision, not re-asked. The startup scan runs after `goalruns` resumes its own runs (`_resume_goals` in `services/opsview/server.py`).
- **Test scenarios:**
  - Happy: thread with unanswered user message at startup gets exactly one assistant message after resume (fake engine).
  - Edge: a thread whose run finished while the console was down is resumed with the run output in context.
  - Edge: a thread whose run is still running re-attaches and does not start a second run.
  - Error: a second restart mid-resume does not loop; the thread shows `interrupted` and Retry works.
  - Negative control: with the startup scan disabled, the first scenario fails.
- **Verification:** `uv run --locked pytest tests/opsview/test_threads.py`; ruff clean; hygiene test passes.

### U2. Streaming replies

- **Goal:** Text appears as the model writes it. (R2)
- **Requirements:** R2, R13, R14
- **Files:** `services/openrouter/client.py` (add `chat_stream`), `services/opsview/threads.py`, `services/opsview/server.py` (SSE route `GET /api/threads/<id>/events`), `ui/src/lib/api.ts`, `ui/src/components/ThreadView.tsx`, `tests/opsview/test_thread_stream.py`, `tests/unit/test_openrouter_stream.py`, `tests/e2e/spa.e2e.mjs`
- **Approach:** The existing `post()` buffers the whole body, so add a line-reader over the response, a mid-stream error path that records a failed call row, and `stream_options.include_usage` for the final usage chunk. The thread `Deps.engine` signature changes from `list[dict] -> str` to a streaming callable that yields deltas and returns usage; keep a thin adapter so existing `tests/opsview/test_threads.py` fakes still work. `chat_stream` yields parsed deltas and records one call row with usage at the end (same `kind` and ledger path as `chat`). The turn loop publishes events to a per-thread in-memory log with `seq`; the SSE route replays from `Last-Event-ID` then tails. The loop in `services/workspace/routes.py` (`g_events`) is not reusable as is; extract its replay, tail and heartbeat loop into a shared helper and give threads their own in-memory log. The log is lost on restart, so a stale `Last-Event-ID` gets an `event: reset` plus a snapshot built from the thread file, mirroring how `g_events` handles a rewound stream. Cancel acts between chunks or by closing the response, never inside a blocked read. ThreadView appends deltas to the pending bubble, shows a calm single-line "working" state with the one accent, and falls back to polling if the stream errors twice.
- **Test scenarios:**
  - Happy: scripted chunks arrive in order over SSE and the final transcript equals the polled transcript.
  - Edge: client reconnects with `Last-Event-ID` and receives no duplicates and no gaps.
  - Edge: partial JSON chunk boundaries and a final usage-only chunk parse correctly.
  - Error: upstream error mid-stream yields `turn.error` and a retryable message, never a half-bubble stuck forever.
  - UI e2e: streaming bubble renders, then settles; light and dark screenshots; axe 0 violations; reduced-motion respected.
- **Verification:** pytest for the three test files; `ui` typecheck and build; e2e with fixture data only.

### U3. Stop and steer

- **Goal:** One action stops a turn; a message sent mid-turn redirects it. (R3)
- **Requirements:** R3, R14
- **Files:** `services/opsview/threads.py`, `services/opsview/server.py` (`POST /api/threads/<id>/stop`), `ui/src/components/ThreadView.tsx`, `tests/opsview/test_threads.py`
- **Approach:** `services/opsview/goalruns.py` has no run cancel today (the budget pause deliberately cancels nothing), so this unit adds one first: a stop flag checked by `_drive` and the worker loop, and a terminal `cancelled` state surfaced by `get_run` and `run_status`. Decision: a stopped turn cancels its run (the owner pressed stop); pause-at-cap behavior is unchanged. Cancel token per KTD4; stop cancels the model stream and the linked run and writes an `interrupted` message. Composer swaps Send for Stop while working; typing and sending while working queues a steer message shown as "queued". The model sees the steer message at its next decision point.
- **Test scenarios:**
  - Happy: stop during streaming ends the turn within one chunk and cancels the run.
  - Happy: steer message sent mid-turn appears in the next model call's messages.
  - Edge: stop on an idle thread is a no-op; double stop is idempotent.
  - Error: stop while a run waits for approval cancels the approval request cleanly.
  - Negative control: without the cancel check, the stop test times out.
- **Verification:** pytest; e2e stop and steer flow.

### U4. Inline approval card

- **Goal:** Approve or reject without leaving the thread. (R4)
- **Requirements:** R4, R14
- **Files:** `ui/src/components/ThreadView.tsx`, `ui/src/pages/Approvals.tsx` (share the card), `services/opsview/server.py`, `tests/e2e/spa.e2e.mjs`, `tests/opsview/test_threads.py`
- **Approach:** Today `wait_for_run` returns on an approval status and the turn ends (`_STOP_NOTES`). Change it so the turn stays alive across a pending approval: it emits `approval.requested`, waits, and continues on approve or reject (with a timeout that ends the turn with a visible note). Reuse the #96 card fields in a shared component. The thread emits `approval.requested` carrying the pending approval id; the card posts to the existing approve and reject endpoints, so the hash binding, single use and expiry are untouched. This is the one screen accent: the card is the "needs you" item.
- **Test scenarios:**
  - Happy: a run with a pending approval shows the card; approve continues the turn.
  - Error: reject ends the step and the model is told it was rejected.
  - Edge: a stale or already-decided approval shows its final state, and the endpoint refuses a replay.
  - Safety: the inline card cannot approve a payload whose hash differs from the one shown (existing gate test, added for the new path).
- **Verification:** pytest gate and thread tests; e2e with light and dark screenshots.

### U5. Routing truth shown in the thread

- **Goal:** The owner can see which model answered and what it cost, and local or private routing is never silent. (R6)
- **Requirements:** R6, R14
- **Files:** `services/placement/rules.py`, `services/placement/health.py`, `services/orchestrator/engines.py`, `services/openrouter/client.py`, `ui/src/components/RunView.tsx`, `ui/src/components/ThreadView.tsx`, `tests/unit/`, `tests/opsview/`
- **Approach:** Follow U1 of `docs/plans/2026-10-31-003-feat-chat-agent-product-plan.md` (not this plan's U1): the chosen effort model drives every step; local models only when `OC_LOCAL_MODELS` is set; a PII-flagged step is held for approval; each assistant message and step stores `model_served` and cost from the call row. The thread shows a quiet one-line footer per turn (model, cost), expandable.
- **Test scenarios:** placement table cases with and without `OC_LOCAL_MODELS`; PII step is held; served model equals the model in the call row for a fake engine that reports a different served id; footer renders from stored data. Each case fails against a broken fake that routes locally by default.
- **Verification:** pytest unit and opsview; e2e screenshot of the footer in both themes.

### U6. Native tool calling and typed tools

- **Goal:** The orchestrator acts through typed tools, not parsed JSON. (R5)
- **Requirements:** R5, R13
- **Files:** `services/orchestrator/chat.py`, `services/orchestrator/tools/__init__.py`, `services/openrouter/client.py` (tools param, tool-call parsing in stream), `services/router/model_catalog.*` (`supports_tools`), `tests/unit/test_chat_tools.py`
- **Approach:** Tool registry with JSON schemas for `run`, `ask`, `reply`. The loop executes tool calls, appends tool results, and continues until the model stops calling tools or the per-message run budget is spent. Models without `supports_tools` use the existing decision parser. Existing `test_threads.py` scenarios must pass unchanged on the fallback path.
- **Test scenarios:** scripted tool-call stream starts a run and the result is fed back; two parallel tool calls; invalid arguments return a tool error the model can see; run budget exhausted forces a reply; fallback path parity.
- **Verification:** pytest; the full opsview suite stays green.

### U7. Shell: grouped navigation, Developer area, open on the composer

> **Supersession (everyday shell IA):** Primary nav labels and “honest empty” Artifacts/Plugins stubs are superseded by `docs/plans/2026-11-06-1222-feat-everyday-chat-ui-plan.md` (KTD1, U2). This unit still owns React full-move leftovers (`spa: false` operator pages, `/developer/*` when that lands). Do not add Search/Computer chrome or fake destinations here.

- **Goal:** Remove the clutter before the chat. (R7)
- **Requirements:** R7, R14
- **Files:** `ui/src/components/Sidebar.tsx`, `ui/src/components/NavList.tsx`, `ui/src/App.tsx`, `ui/src/pages/` (operator pages re-routed under `/developer/*`), `tests/e2e/spa.e2e.mjs`
- **Approach:** Everyday chat-first rail per the everyday-chat plan; operator pages under progressive disclosure / Developer. Old routes redirect so bookmarks work. Collapse remembers state; mobile uses a drawer.
- **Test scenarios:** every old operator URL redirects; keyboard navigation and focus order; collapsed state persists; mobile 390 layout; light and dark screenshots; axe 0 violations.
- **Verification:** ui build; e2e.

### U8. Junk control and thread hygiene

- **Goal:** Recents contains only conversations worth keeping. (R12)
- **Requirements:** R12, R14
- **Files:** `services/opsview/threads.py`, `services/opsview/server.py`, `ui/src/components/Sidebar.tsx`, `tests/opsview/test_threads.py`
- **Approach:** Auto-title from the first exchange (model call through the injected engine, with a truncated-first-line fallback); threads with no assistant reply after a failed start are hidden and swept after a grace period; pin and unpin; a thread search over titles and message text; retention setting (default off) that archives threads idle for N days.
- **Test scenarios:** empty and failed-at-start threads absent from the list; pinned sort first; search hits message text; retention archives only idle, unpinned threads; title fallback with a failing engine.
- **Verification:** pytest; e2e light and dark.

### U9. Web search and fetch tools

- **Goal:** The model can look things up. (R8)
- **Requirements:** R8, R13
- **Files:** `services/orchestrator/tools/web.py`, `services/gate/` (egress policy hook, read-only), `tests/unit/test_web_tools.py`
- **Approach:** `web_search` and `web_fetch` behind an injected provider; fetch honors the egress allowlist and strips active content; results are size-capped and cited with their URLs. Search mode in the composer (U11) uses only these tools.
- **Test scenarios:** fake provider results are cited; a host outside the allowlist is refused with a clear error; oversized pages truncate; redirect to a disallowed host is refused. Negative control: with the allowlist check removed the refusal test fails.
- **Verification:** pytest; no network in tests.

### U10. Files and artifacts tools, Artifacts page

- **Goal:** Work products are first-class and findable. (R8, R9)
- **Requirements:** R8, R9, R14
- **Files:** `services/orchestrator/tools/files.py`, `services/opsview/server.py`, `ui/src/pages/Artifacts.tsx`, `tests/unit/`, `tests/e2e/spa.e2e.mjs`
- **Approach:** Per-thread workspace with read, write, list and `publish_artifact`; path traversal refused; Artifacts page lists published artifacts with open and download, grouped by thread.
- **Test scenarios:** traversal and symlink escape refused; publish appears in the list; deleting a thread offers to keep or remove its artifacts.
- **Verification:** pytest; e2e screenshots both themes.

### U11. Home composer and pickers

> **Supersession:** Search/Computer mode toggle and attach chrome are rejected/deferred by `docs/plans/2026-11-06-1222-feat-everyday-chat-ui-plan.md` (KD1, KD3, U3). Keep effort + Advanced; optional catalog chip when pickers are ready. Do not implement a mode toggle for parity.

- **Goal:** One calm box with the choices that matter and nothing else. (R7, R14)
- **Requirements:** R7, R14
- **Files:** `ui/src/components/Composer.tsx`, `ui/src/components/EffortPicker.tsx`, `tests/e2e/spa.e2e.mjs`
- **Approach:** Effort levels remain the default spend control; model/budget under Advanced (or a catalog chip fed by `GET /api/models` when OC-087 pickers land); dictate. No Search/Computer toggle; no attachment button until a backend phase exists. Keep the one-box experience: nothing but the box is required to send.
- **Test scenarios:** models above the spend ceiling are not selectable; keyboard-only send; screenshots both themes at both widths; assert no Search/Computer control.
- **Verification:** ui build; e2e.

### U12. Usage and Settings pages

- **Goal:** Spend and control in one place. (R9)
- **Requirements:** R9, R12, R14
- **Files:** `ui/src/pages/Settings.tsx`, `ui/src/components/UsagePopover.tsx`, `services/opsview/server.py` (usage read from the cost ledger), `tests/opsview/`
- **Approach:** Usage popover from the ledger (today, cap, per model); Settings holds default model, local-model opt-in, retention, theme, and a Developer link. Few visible actions: each setting saves on change.
- **Test scenarios:** usage totals equal ledger rows for a fixture; settings round trip; local-model opt-in toggles the placement behavior tested in U5.
- **Verification:** pytest; e2e.

### U13. Subagents with carved budgets

- **Goal:** The model can delegate without losing cost control. (R8)
- **Requirements:** R8, R13
- **Files:** `services/orchestrator/subagent.py`, `services/cost/`, `tests/unit/test_subagent.py`
- **Approach:** `spawn_subagent` tool carves a budget from the parent's remaining cap; allow-list of models from the catalog; results return as tool output; depth limit 1.
- **Test scenarios:** carved budgets never exceed parent remaining; child pause does not cancel the parent; depth limit enforced; spend rule enforced on the child's model.
- **Verification:** pytest.

### U14. Context compaction

- **Goal:** Long threads keep working. (R10)
- **Requirements:** R10, R13
- **Files:** `services/orchestrator/context.py`, `tests/unit/test_context.py`
- **Approach:** When the thread exceeds a token budget, summarize the oldest span into a pinned summary message through the injected engine; decisions, open questions and run ids are carried verbatim.
- **Test scenarios:** summary retains marked decisions; token count drops below budget; compaction is idempotent; failure of the summarizer leaves the thread unchanged.
- **Verification:** pytest.

### U15. MCP client, Plugins page, automations into threads, CLI

- **Goal:** Ecosystem reach and one product surface. (R9, R10)
- **Requirements:** R9, R10
- **Files:** `services/mcp/` (extend the existing server package with a client module), `ui/src/pages/Plugins.tsx`, `services/opsview/automations.py`, `oc/cli/`, `tests/`
- **Approach:** Stdio MCP client whose tools register into the U6 registry behind an owner-approved allow-list; automations create or post to threads and the goal-run DAG is retired with a migration test that keeps legacy runs readable; `oc chat` and `oc threads` use the same API. Decide at this unit whether threads move off JSON files (KTD2) and whether OC-078 is still needed.
- **Test scenarios:** MCP fake server tools appear and are gated; automation fires into a thread; legacy run read-through; CLI exit-code contract.
- **Verification:** pytest; e2e; hygiene.

### U16. Safety-net lane A

- **Goal:** Gate, audit and operator views have tests that fail when broken. (R11)
- **Requirements:** R11
- **Files:** `services/gate/`, `tests/unit/`, `tests/opsview/`, `tests/fixtures/`
- **Approach:** OC-021 hash-chained audit log with `oc audit verify`; OC-016 wire the unused policy fixture and resolve the benign-egress contradiction; OC-025 and OC-026 ops view and approval console golden renders plus escape and no-mutation tests. Split into one PR per row.
- **Test scenarios:** tampering with one audit record breaks verify; the policy fixture is exercised by the U5 PII-hold test; golden renders differ against a mutated renderer.
- **Verification:** pytest; mutation check on a broken fake for each row.

### U17. Safety-net lane B

- **Goal:** Router, budget and chaos paths are proven offline. (R11)
- **Requirements:** R11, R13
- **Files:** `services/router/`, `tests/unit/`, `tests/conformance/`
- **Approach:** OC-017 router golden replay over the 180-fixture corpus with a fallback matrix; OC-018 router and budget unit tests with negative controls; OC-024 chaos and timeout suite; OC-028 branch-coverage gate on safety paths; OC-037 router cleanup. One PR per row.
- **Test scenarios:** the replay fails against a router that misroutes one fixture; budget overrun pauses and never cancels; timeout injection yields the documented error class.
- **Verification:** `uv run --locked pytest -m "not live" -p no:cacheprovider`; coverage gate reported by the command that adds it.

---

## Verification Contract

- Python: `uv run --locked ruff check .` and `uv run --locked pytest -m "not live" -p no:cacheprovider`, both exit 0, checked per command rather than through a pipe.
- UI: `pnpm --dir ui typecheck` and `pnpm --dir ui build`.
- Console e2e: `npm test` in `tests/e2e` (Playwright plus axe) against fixture data only (no microVMs, no models, loopback). Screenshots from `tests/e2e/shots.mjs` (extend its page list and fixture threads for thread views), light and dark, 1440 and 390 wide, attached to each UI PR.
- Hygiene: `tests/unit/test_obs_open_source_hygiene.py` passes.
- No unit may add a test marked `live` that runs in CI, and none may make a model, search or network call in a default test run.

## Definition of Done

- Global: R1-R14 satisfied; each unit merged as its own PR with green CI; ROADMAP.md rows touched by a unit (OC-068..OC-077, OC-079..OC-082, OC-084, OC-085, OC-088..OC-091 and the U16/U17 rows; not OC-078 or OC-083) updated to their real state in the PR that completes them; abandoned-attempt code removed from every diff.
- Daily-driver milestone (after U5): restart the console mid-turn and the thread recovers; a reply streams; a turn can be stopped and steered; an approval is answered inline; the footer names the serving model.
- Per unit: its Test scenarios exist, each negative control fails against the broken fake, and its Verification commands exit 0.

## Open questions

### Resolve before starting (owner)

- Q1. Confirm the merge order #109, #110, #111, and that #110 retargets to `main` after #109 merges.
- Q2. Is the Cloudflare-hosted frontend (OC-092) wanted now? It is sequenced last; say yes to pull it forward.
- Q3. Grok 4.7 sits above the spend-rule ceiling in the catalog (OC-087). Raise the ceiling, or keep it `above_ceiling`?
- Q4. Should Search mode ship in v1 of the composer (U11 waits on U9), or is Computer mode alone enough for the first daily-driver release?

### Deferred to implementation

- Exact SSE heartbeat interval and event-log retention (U2).
- Which catalog models set `supports_tools` (U6), decided from provider metadata, not from live calls.
- Whether file-based threads survive past U15 (KTD2).
