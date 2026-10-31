---
title: One goal box with an effort picker - Plan
type: feat
date: 2026-10-31
execution: code
---

# One goal box with an effort picker - Plan

## Goal

Make the console work like Perplexity Computer: one box where you type the goal, a small Low / Medium / High
picker that chooses the model and the spend limit, and everything else behind one **Advanced** disclosure with
sane defaults.

**Out of scope:** merging, deploying, the agent-job runner wiring, scheduled goals (Automations), any change to the
spend rule.

## What the survey found (read this first)

The premise "the console has a huge list of settings" is only partly true. Verified against `main` at `7467ad6`:

- The console has **no settings page**, and `/runs/new` has **no fields**: it is one confirm button for the
  conformance matrix (`views.page_run_confirm`).
- The goal composer had **one input**, the goal text. Model and budget were not choosable at all: the planner and every
  cloud step used a hard-coded `z-ai/glm-5.3-flash` (`services/orchestrator/engines.py`), and a goal run had **no per-run
  budget** (the orchestrator is built without a cost ledger in `goalruns.submit`).
- The real "huge list" is **58 environment variables** read across `services/`, `oc/` and `scripts/` (46 are `OC_*`),
  none visible in the UI.
- There were **two separate submit boxes** with different knobs: the "Computer" composer (goal only) and the
  "Agent jobs" form (goal plus a budget in dollars, model fixed). That split is the part that feels like a long list.

### Every operator-facing input in `ui/src`

| Where | Field | Goes to | Consumed by |
|---|---|---|---|
| Composer (`/`) | Goal text, 4000 chars | `POST /goals` | `server.p_goals` -> `_submit_goal` -> `goalruns.submit` |
| Composer | "Computer" pill | nothing (a label) | **removed**, replaced by the effort picker |
| Composer | Dictate button | `POST /api/transcribe` | `server.p_transcribe`, `GROQ_API_KEY` |
| Composer | 3 starter cards | fills the goal text | client only |
| Confirm card | Run it / Cancel | `confirm=1` on `POST /goals` | `p_goals` |
| Agent jobs | Goal text | `POST /api/agent-jobs` | `agentjobs.submit` -> `JobRunner.submit` |
| Agent jobs | Budget in dollars (blank = $25) | same | `runner.resolve_budget`, ceiling `OC_AGENT_MAX_BUDGET`; model fixed at `deepseek/deepseek-v4-pro` |
| Agent jobs | Approve / Deny, optional note | `/api/agent-jobs/<id>/approvals/<aid>/{approve,deny}` | `agentjobs.decide` (decisions, not settings) |
| Router | Utterance, 500 chars | `POST /api/router/test` | `server._route`; `OPENROUTER_API_KEY` decides paid or table engine |
| Automations | Goal text | `POST /automations` | `automations.create` (Temporal schedule) |
| Automations | Every N minutes, 5 to 10080, default 60 | same | same |
| Audit | `f` filter, `q` text (200 chars) | `GET /api/audit` | `consoleapi.audit_payload` (view filter, read-only) |
| Approvals | Approve / send back, optional note | `/api/approvals/<id>/...` | `ApprovalQueue.decide` |
| Devices, Sandboxes, Skills, Overview, Runs | none (read-only) | `GET /api/*` | n/a |

### Environment variables by area (all stay env vars; none is a UI setting)

| Area | Variables |
|---|---|
| Spend | `OC_DAILY_CAP` (default $1.00; also the effort ceiling below), `OC_TASK_CAP` ($0.10, router per-decision), `OC_RUN_CAP` ($0.10, MCP per-run), `OC_AGENT_MAX_BUDGET`, `OC_AGENT_APPROVAL_TTL_S`, `OC_AGENT_RESUME_MAX_AGE_S` |
| Models and keys | `OPENROUTER_API_KEY`, `OPENROUTER_BASE`, `OC_OPENROUTER_*` (7 attribution fields, `OC_OPENROUTER_BASE_URL`), `OC_LOCAL_MODEL`, `OC_OLLAMA_MODEL`, `GROQ_API_KEY` |
| Sandboxes | `CUBE_API_KEY/URL/TEMPLATE_ID`, `SPACE_*` (5), `OC_EXECPLANE_BACKEND`, `OC_EXECPLANE_DIR` |
| Paths and state | `OC_ROOT`, `OC_GOALS_DIR`, `OC_RUNS_DIR`, `OC_AUDIT`, `OC_APPROVALS`, `OC_ROUTER_LEDGER`, `OC_OBS_DIR`, `OC_EVENTS_DIR`, `OC_WORKSPACE_DIR`, `OC_REPLAY` |
| Durability | `OC_DURABLE`, `OC_TEMPORAL_HOST`, `OC_TEMPORAL_NAMESPACE` |
| Auth | `OC_CSRF_SECRET`, `OC_ACCESS_IDENTITY_URL` |
| Logging and telemetry | `OC_LOG_*` (5), `OC_DEBUG`, `OC_OBS_CAPTURE_CONTENT`, `OC_SENTRY_DSN`, `OC_TALLYLINE_*` (3) |

## Proposal

### 1. Effort levels

One module, `services/opsview/effort.py`, is the single source of truth. Each level names a router tier from
`services/router/policy_table.py`; the model is that tier's first candidate in `TIER_MODELS`, so the spend rule applies
to it automatically. The budget is a hard per-run cap on the existing `CostLedger` (`services/cost`), clamped to
`OC_DAILY_CAP`.

| Effort | Router tier | Model (planner and cloud steps) | Budget per run | For |
|---|---|---|---|---|
| Low (default) | `cheap` | `google/gemini-2.5-flash` | $0.10 | quick, routine work |
| Medium | `mid` | `deepseek/deepseek-v4-pro` (the orchestrator default) | $0.30 | harder tasks |
| High | `frontier` | `x-ai/grok-4.7` (the price ceiling) | $1.00 | most capable allowed |

- Spend rule: nothing above Grok 4.7 is reachable; no level or override is premium; High is never the default
  (`OC_EFFORT_DEFAULT` may pick Low or Medium, anything else falls back to Low). `tests/unit/test_spend_rule.py` gains
  a test for all three.
- Budget behavior is the ledger's, as designed for OC-035: reaching the budget **pauses** the run, nothing is
  cancelled, and the run page says so. A run in flight can overshoot by one call's real cost.
- The model applies to the planner and to cloud steps. Steps placed on a local model keep `OC_LOCAL_MODEL`.
- A run remembers its effort, model and budget on disk (`effort_set` audit row, `cost.ndjson`), so a restart or a
  durable worker resumes with the same model and cap.

### 2. What is in the composer, what is Advanced

| Control | Where | Notes |
|---|---|---|
| Goal text | box | unchanged |
| Effort: Low / Medium / High | bottom of the box | radio group; options come from `GET /api/effort`, so the UI carries no model or price list |
| Dictate | bottom of the box | unchanged |
| Model | **Advanced** | Automatic, or one of the three models above. Anything else is refused by the server |
| Budget for this run | **Advanced** | blank = the level's budget; must be above 0 and at most `OC_DAILY_CAP` |

Advanced is one disclosure with two fields. A collapsed Advanced says "changed" when an override is active.

### 3. Kept, removed, moved

| Item | Decision |
|---|---|
| "Computer" pill in the composer | removed (it was a label with no behavior) |
| Hard-coded `glm-5.3-flash` for console goal runs | replaced by the effort model; the default stays in `engines.py` for callers that pass no model (MCP, scheduled automations) |
| `OC_DAILY_CAP` | kept as the ceiling for any run budget |
| `OC_EFFORT_DEFAULT` | **new** env var: which level is preselected |
| `OC_TASK_CAP`, `OC_RUN_CAP` (both $0.10) | kept; they equal Low's budget today. Folding them into the effort table is a follow-up, not done here (MCP and router tests depend on them) |
| Agent jobs "Budget in dollars" | kept for now; the job runner is not wired in production, so a submitted job is only queued (see ROADMAP OC-040). Folding it in is the follow-up below |
| All other env vars | kept as env vars; none belongs in a UI |

### 4. API

- `GET /api/effort` returns `{default, max_budget, models, levels[{id,label,blurb,model,budget}]}`; side-effect free.
- `POST /goals` accepts `effort`, `model`, `budget` in the JSON body. The confirm step now returns the resolved
  `{effort, model, budget}` and the confirm card shows them before any model is called. Unknown effort, a model outside
  the allow-list, or a budget above the cap returns 422 and submits nothing.
- `GET /api/runs/<id>` gains `effort: {id, model, budget, spent, paused}` for runs started with one.

## Decisions for the owner

1. **Default level.** Low (cheapest, never premium) or Medium? Today's effective default is `glm-5.3-flash`, which is not
   in `TIER_MODELS`; Low now uses `gemini-2.5-flash`, so Low is not byte-for-byte today's behavior.
2. **Level budgets** ($0.10 / $0.30 / $1.00) and whether High may equal the daily cap.
3. **Paused runs have no resume button.** The ledger supports raising the cap; the console has no control for it.
   Add "Raise budget and continue" next?
4. **Agent jobs.** When the production runner exists, should High route to an agent job so there is one box, or keep Agent
   jobs as its own page?
5. **Automations** (scheduled goals) still run with the engine default and no budget. Give them an effort picker?

## Verification

- `tests/unit/test_effort.py`, `tests/opsview/test_effort_api.py`: mapping to tiers, refusal of premium, unknown and
  over-cap input, the daily-cap clamp, the confirm step, a run pausing at its budget, resume keeping model and cap.
- `tests/e2e/spa.e2e.mjs`: default selection, High carried through confirm, keyboard-only flow, Advanced overrides and
  the over-cap block, the effort list failing to load, the run page's effort line and budget pause, and axe (WCAG 2.2 AA)
  at 1440 and 390 px in light and dark, closed, open and in the error state.
