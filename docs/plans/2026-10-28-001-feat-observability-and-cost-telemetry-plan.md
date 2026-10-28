---
title: Observability and OpenRouter cost telemetry — staged plan
type: feat
date: 2026-10-28
origin: docs/brainstorms/2026-10-28-observability-and-openrouter-cost-requirements.md
status: Stages 1 and 2 built (see docs/runbooks/observability.md); Stages 3-5 gated on operator decisions in the origin doc; Stage 6 is a later side track, not scheduled
---

# Observability and OpenRouter cost telemetry

## Summary

Six stages. Each ships alone, is green in CI, and is useful without the next. Stage 1 fixes the defect that
matters most (real spend never reaches the cost ledger) and lays the id spine and attribution every later stage
rides on. Stages 2-5 widen logging, export to Grafana, and loop the data back into the console. Stage 6 is a
separate side track (free-model router harness) and is **not** part of the current build.

Requirement ids (R1-R10) are from the origin brainstorm.

## Design decisions (apply to every stage)

- **General, configurable, no personal setup.** This is an open-source project. Nothing in code, defaults,
  tests, sample configs or docs may name one operator's hosts, domains, tunnels, keys or machines. Config is
  `OC_*` env vars (plus standard `OTEL_*` for OTLP) with defaults that work on a fresh checkout: local files
  under `OC_OBS_DIR` (default `~/.local/share/oc/obs`), stderr logs, all exporters off. A lint test greps the
  tree for a deny-list of operator-specific strings. Docs are written for strangers.
- **Stdlib first.** Core of `oc/obs` uses `logging`, `contextvars`, `json`, `urllib`. The repo has one runtime
  dependency (`fido2`); we keep it that way. OpenTelemetry SDK arrives in Stage 3 as an **optional extra**
  (`[project.optional-dependencies] otel`), imported lazily; absent = silent no-op. Same pattern as `space`.
- **Local NDJSON is the record; exporters are views.** Mirrors the ledger/audit/event-stream design already in
  the repo. Append-only, `flock` for writers, torn-last-line tolerant readers.
- **One id spine** (R1): `trace_id` (32 hex, derived deterministically from `run_id` via sha256 so a restarted
  process, `Orchestrator.recover`, and durable activities land on the same trace with no new state), `run_id`,
  `subtask`, `chat_id`, `service`. Carried by `contextvars` in-process and by explicit fields on the fsproto
  task body across the worker boundary (the worker may be another process).
- **Redaction at the sink** (R2). One `redact()` runs on every record before serialization: key-name rules
  (`*key*`, `*token*`, `*secret*`, `authorization`, `password`, `cookie`), value-pattern rules (OpenRouter and
  other `sk-…` keys, `Bearer …`, JWT shape, `gh*_…` tokens, AWS ids, emails, phone numbers), plus exact-match
  scrubbing of the *values* of any env var whose name looks secret. Prompt/completion text is never logged unless
  `OC_OBS_CAPTURE_CONTENT=1`, and then still redacted.
- **Failure isolation** (R9). Any obs failure is swallowed and counted; it never raises into a run.
- **Cost truth hierarchy:** (1) provider `usage.cost` from the response, (2) generation stats API at reconcile
  time, (3) never a price table. A call with no reported cost is recorded `cost_source: "none"` and surfaced.
- **Attribution on every OpenRouter call** (R10), one helper, one place (`services/openrouter/attribution.py`):

  | Env | Default | Effect |
  |---|---|---|
  | `OC_OPENROUTER_ATTRIBUTION` | `on` | `off` sends no attribution headers or `user` |
  | `OC_OPENROUTER_REFERER` | this project's public repo URL | `HTTP-Referer` (the app identity) |
  | `OC_OPENROUTER_TITLE` | `Open Computer` | `X-OpenRouter-Title` (and `X-Title` for older clients) |
  | `OC_OPENROUTER_CATEGORIES` | unset | `X-OpenRouter-Categories` (max 2, validated against the documented list) |
  | `OC_OPENROUTER_APP_VISIBILITY` | unset (public) | `hidden` adds `X-OpenRouter-App-Visibility: hidden` (only effective when the app is first created) |
  | `OC_OPENROUTER_USER` | unset | opaque value hashed (sha256, truncated) into the request `user` field; never sent raw |
  | `OC_OPENROUTER_ENVIRONMENT` | unset | `trace.environment` |

  Per request also: `session_id` = `chat_id`, and `trace` = `{trace_id, trace_name, span_name, generation_name,
  parent_span_id (Stage 3), environment, oc_run, oc_subtask, oc_chat}`.
- **Spend rule untouched.** `tests/unit/test_spend_rule.py` still governs model defaults.

## What is possible with OpenRouter (summary; sources in the origin doc)

- Filter usage **by app** in Activity and in `POST /api/v1/analytics/query` (dimension `app`, numeric app id) once
  `HTTP-Referer` (+ title) is sent. App pages, rankings and model "Apps" tabs are public unless the app is
  created `hidden`.
- Per-**user** (`external_user`) and per-**session** (`session_id`) data on every generation; free-form `trace`
  keys go to every Broadcast destination but are **not** analytics dimensions inside OpenRouter.
- Broadcast: traces only, async, per-destination API-key filter, sampling, Privacy Mode; Grafana Cloud (Tempo),
  generic OTel Collector, S3-compatible, Langfuse and many others. Owner sets it up in the dashboard.
- Read-side analytics need a **management** key (separate from the inference key). Optional, Stage 4/5.

## Stages

### Stage 1 — Logging foundation, attribution, and OpenRouter cost capture  (R1-R5, R9, R10; built)

**Goal:** every OpenRouter call leaves a complete, attributable, redacted local record, carries attribution, and
the cost ledger records provider-reported spend.

Work:
- `oc/obs/`: `context.py` (bind/get ids), `redact.py`, `log.py` (JSON formatter, `get_logger(service)`,
  `OC_LOG_LEVEL`, `OC_DEBUG=1`, per-service override `OC_LOG_LEVEL_<SERVICE>`, stderr + rotating NDJSON file
  under `OC_LOG_DIR`).
- `services/openrouter/`: `attribution.py` (above), `client.py` (`chat()` → `ChatResult`: text, usage, generation
  id, latency, served model, provider, finish reason; typed errors that still record a call row), `store.py`
  (`CallStore` NDJSON + `summarize(group_by=chat|run|subtask|model|provider|day)`), `generation.py`
  (`fetch_generation` with backoff, `reconcile()` → `reconciled|mismatch|missing`), `__main__.py`
  (`report`, `reconcile` CLI).
- Wire `services/orchestrator/engines.py`: `_chat` goes through the client; the flat `$0.30/Mtok` guess leaves
  the accounting path; cloud workers report real `Usage` so `CostLedger.record()` runs (including the sandbox
  script-writer call). `runner.run_once` binds context from the task body; `Orchestrator.submit` binds context
  around `decompose` (planner spend attributed to the run) and accepts `chat_id`.
- `services/router/decide.py::ask_decisions` and `bench/router/*` use the same attribution helper and write a call
  row (`kind: "decision"`).

Tests: redaction (keys, patterns, env-value scrub, nested; no seeded secret ever appears in captured logs),
context propagation across threads, JSON shape, attribution headers present on **every** call site and absent
with `OC_OPENROUTER_ATTRIBUTION=off`, `user` is hashed, call-row completeness against a fake OpenRouter HTTP
server (success, HTTP error, malformed body, missing cost), ledger receives provider cost and `paused_cap`
triggers on real spend, reconcile statuses, store survives a torn last line, obs failure does not fail a run,
deny-list lint for operator-specific strings.

Exit: CI green; `python -m services.openrouter report --by chat` works on a seeded dir; cap pauses on actual
(not estimated) spend in a unit test.

### Stage 2 — Logging across all services  (R1, R2; built)

**Goal:** the ten services speak the same log dialect, and `OC_DEBUG=1` is genuinely useful.

| Service | Must log (INFO unless noted; DEBUG adds inputs and decisions) |
|---|---|
| orchestrator | run submit/recover/complete, decompose result (counts, not text), dispatch, dep-fail, tombstones |
| router | decision (route, tier, engine, confidence, cost, ms), engine down + fallback (WARNING) |
| placement | target, reason, headroom, inputs summary at DEBUG, blocked (WARNING) |
| gate | scan categories (never matched text), approvals requested/decided, replay attempts (WARNING) |
| cost | reserve/release/record/pause/resume with run, cap, spend |
| execplane | probe, sandbox create/exec/close, egress allowlist hosts, backend errors, durations |
| device (+ relay, agent) | register, heartbeat gaps, envelope seal/verify results, grant use, parked/drain |
| mcp | tool call name, args shape (redacted), result status, latency |
| opsview | per-request access log (method, route template, status, ms, actor), auth outcomes, CSRF rejects |
| telemetry | send attempts and failures (currently silent) |

Work: replace remaining `print()` with the logger; request-id middleware in opsview; `python -m oc.obs tail
--trace <id> | --run <id> | --level` reader that merges log, `orchestrator.ndjson`, ledger, and event stream by
`trace_id`; lint test forbidding new bare `print()` in `services/` and `oc/`; fuzz-style redaction test over all
logs captured in the e2e suite; a runbook "diagnose a failed run from one trace id".

### Stage 3 — OpenTelemetry + Prometheus export, sample Grafana stack  (R6)  — operator decision 1

- `oc/obs/otel.py` (lazy, optional extra): OTLP/HTTP exporters for traces and logs, standard `OTEL_*` env;
  spans run → subtask → `openrouter.chat` with GenAI semantic-convention attributes (`gen_ai.request.model`,
  `gen_ai.usage.*`, `gen_ai.provider.name`) plus `oc.cost_usd`, `oc.run_id`, `oc.chat_id`.
- Sends `trace.trace_id` and `trace.parent_span_id` so Broadcast spans nest under ours (same trace in Tempo).
- `GET /metrics` on opsview (Prometheus text computed from the call store, no client lib): cost, tokens by kind,
  duration histogram, errors by class, cap-pause counter, `oc_obs_dropped_total`. Run/chat ids are **never**
  metric labels (cardinality); they live in traces, logs and the store. Off unless `OC_METRICS=1`; same auth as
  other opsview routes.
- `deploy/obs/` **sample** stack (Grafana, Tempo, Loki, Prometheus, OTel Collector/Alloy): provisioned
  datasources and dashboards-as-JSON (cost by model/provider, latency p50/p95, error rate, cap burn). Binds to
  localhost by default; README explains exposing it and pointing the same app at Grafana Cloud or any OTLP backend
  with three env vars.
- Tests: exporter off = zero imports; exporter failing = run unaffected; metrics text valid and golden; compose
  and dashboard JSON parse.

### Stage 4 — OpenRouter Broadcast + reconciliation loop  (R5, R7)  — operator decision 2

- Runbook `docs/runbooks/openrouter-broadcast.md`: Settings > Observability steps (account-owner action),
  destination recipes (Grafana Cloud OTLP gateway, generic OTel Collector, S3-compatible bucket, Langfuse),
  Privacy Mode on for anything shared, sampling, API-key filter so only the key this app uses flows.
- `reconcile` runs on a schedule the operator chooses (documented cron/systemd/timer examples): re-fetch
  unreconciled rows older than N minutes, write `reconciled|mismatch|missing`, console banner on mismatch or
  `usage_is_estimated`.
- Optional read-side: with an operator-supplied **management** key, query the analytics API (grouped by `app`,
  `model`, `provider`, `external_user`) and compare daily totals to the local store; discrepancies surface.
- Gate: confirm Broadcast cost matches local cost for a week before anything relies on it.

### Stage 5 — Loop back into opsview + BI  (R8)

- `/costs` page and `/api/costs` (same session + CSRF posture as existing routes): per-chat, per-run, per-goal,
  per-model, per-provider tables; time series; cap burn; top spenders; anomalies (cost-per-run outlier, retry
  storm, estimated usage); drill-down to the exact generations and the log tail for that trace.
- Per-chat cost chip on the goal timeline (reuse the event-stream `cost` event; add `generation_id`).
- BI: `GET /api/costs/export.csv`, periodic Parquet export under `OC_OBS_DIR/export/` (DuckDB-readable), sample
  Grafana dashboards reading the same data.
- Alerts: sample Grafana alert rules and an optional generic webhook (`OC_ALERT_WEBHOOK_URL`) for budget burn.

### Stage 6 (later side track, not scheduled) — Free-model router harness

**Idea.** Run the whole orchestrator on OpenRouter's free models (`openrouter/free`, or explicit `:free`
variants) at **zero model cost**, and make up for weaker models with engineering: heavy retries, failure
handling, context-loss recovery, verification of every step, and an **eval loop** that replays golden tasks over
and over and tunes the configuration to push the success rate up.

**What the platform gives us (checked 2026-10-28; verify again before building, these change).**
- `openrouter/free` picks a free model at random per request, filtered for the features the request needs (vision,
  tool calling, structured output); context up to 200k but it is whatever the picked model has. It falls back only
  to other free models. The served model is in the response `model` field, so we can log which one answered.
- `:free` variants (`<model>:free`) are pinned, so they are the only way to run a *repeatable* eval.
- A `models` fallback array is billed at the paid model's price if a paid model is listed and used. The harness
  must list only free models and a test must assert that no paid slug can enter the array (the spend rule).
- **Rate limits for `:free`:** 20 requests/minute; **50/day under ~$9 of credits ever purchased, 1000/day at or
  above it** (the 1000 tier is a one-time purchase, not a subscription). **Failed attempts count toward the daily
  cap.** `GET /api/v1/key` reports `free_model_daily_requests {used, limit, remaining}`. A `429` can come from
  OpenRouter or from the upstream provider (`error.metadata.provider_code`); honor `Retry-After`. Extra API keys
  or accounts do **not** raise limits (capacity is governed globally).
- Free models can be slower, queue, change, or disappear without notice; no SLA.

**What is realistic.**
- *Budget arithmetic.* One golden task of ~5 steps with planner, worker, verifier and 2 retries is ~20-40
  requests. At 1000/day that is **25-50 task attempts per day**, roughly 3 tasks x 8-15 repetitions. At 50/day it
  is 1-2 attempts: **a free-only eval loop is not viable without the one-time credit purchase**. At 20 rpm a
  single attempt is also floor-limited to ~3 s/request.
- *Capability.* Free models are mostly small or older-generation; random routing adds variance between calls in
  one run. Short, scripted, verifiable tasks (file transforms, extractions, small scripts in a sandbox) are
  plausible; long tool-using chains, strict JSON/tool schemas and tasks needing precise long-context recall are
  where success will drop. **We do not know the baseline.** Making the baseline measurement is the first
  deliverable, and no target number is promised before it. A harness can raise success a lot (verification +
  retry compounds), but it cannot create capability that no available free model has.
- *Cost.* Model cost is $0; the real costs are wall-clock time and the daily request cap. Success-per-request is
  the efficiency metric that matters, not dollars.

**Components.**
1. **Free model catalog:** fetch `/api/v1/models`, keep entries whose prompt and completion pricing are 0 and
   record context length and supported parameters; refresh daily; pin a snapshot per eval run so results are
   reproducible; log the served model per request (via Stage 1 call store).
2. **Resilient call layer:** retry with exponential backoff + jitter, honor `Retry-After`, classify errors
   (429 provider vs platform, 402/403 budget, 5xx, empty/refusal/garbled output, schema failure, truncation by
   `finish_reason`), switch model on repeated failure within a pinned free-only pool, global token-bucket limiter
   at 20 rpm and a **daily-budget governor** that reads `free_model_daily_requests` and refuses to start a run it
   cannot finish.
3. **Context-loss recovery:** every step's inputs/outputs checkpointed to the fs protocol (already durable);
   on a context overflow or a model swap, rebuild the prompt from a structured *state summary* (goal, plan,
   completed step results, open questions) rather than the raw transcript; cap step prompts to the smallest
   context among the pool.
4. **Verification:** per-step verifier (deterministic checks first: exit code, file exists/hash, JSON schema,
   regex, unit tests in the sandbox; model-as-judge only as a tiebreaker and never from the same model that
   produced the answer); a failed verify triggers retry with the verifier's reason appended, bounded.
5. **Golden task suite:** the three golden tasks from the roadmap (OC-040) plus more over time, each with a
   machine-checkable pass condition and a held-out split.
6. **Eval + tuning loop** (`python -m oc.eval`): runs tasks N times per configuration, records per-attempt
   outcome, requests used, latency, served models, failure class; proposes the next configuration by simple
   search (grid / random / one-factor-at-a-time over prompt variants, decomposition granularity, model pool,
   retry limits, temperature) and **keeps it only if it beats the incumbent on the held-out split with a
   confidence interval, not on a lucky run**. Stops on budget, plateau, or max iterations. Outputs a config
   file the router/orchestrator can load.

**Measuring success rate.**
- Unit: *task attempt* = one full run from goal to final artifact. Success = the task's deterministic checker
  passes on the final artifact (no human or LLM grading for the headline number).
- Report: pass@1 (first attempt, no harness help), pass@harness (with retries and verification), attempts and
  requests per success, p50/p95 wall-clock, failure-class breakdown, served-model breakdown, and the number of
  requests wasted on failed attempts (they count against the cap).
- Statistics: Wilson 95% interval on the success rate; with 10 repetitions per task the interval is wide
  (e.g. 8/10 is roughly 49%-94%), so claims need >= 30 attempts per configuration or a paired comparison.
  Because `openrouter/free` is random, report both the pooled result and the pinned-model results.
- Compare against a paid-model reference run (opt-in, small budget, spend rule applies) to show the gap.
- Contamination: keep a held-out set that the tuner never sees.

**Risks.** Daily cap exhaustion (governor), provider-side 429 storms, nondeterminism making tuning noisy
(pinned pool + repetitions), tuner overfitting to the golden tasks (held-out split), free-model terms/data
retention (free endpoints may log prompts; the harness must not send sensitive content and the gate's PII scan
stays in front), model churn invalidating old results (snapshot + date every result).

**Feasibility verdict.** Feasible as a research harness *given the one-time credit purchase for the 1000/day
tier*; **not** feasible on the 50/day tier. The retry, verification and eval-loop machinery is ordinary
engineering and reuses Stage 1 (call store, attribution, reconcile). Whether success reaches a useful level is
an empirical question that only the baseline run can answer.

## Risks (stages 1-5)

| Risk | Mitigation |
|---|---|
| Logs leak a prompt or key | Redaction at the sink; content off by default; a seeded-secret test fails the build |
| Operator-specific strings land in an OSS repo | Deny-list lint test; docs reviewed as for strangers |
| Disk fill from debug logs | Rotating files, size cap, retention job, `OC_DEBUG` off by default |
| Per-chat label explosion in Prometheus | Ids only in traces, logs and the store, never metric labels |
| OpenRouter response shape drifts | Parse defensively; unknown usage fields kept (redacted) in `raw_usage`; `cost_source: none` is visible |
| Generation API is eventually consistent | Reconcile with backoff and a pending state; never block a run on it |
| Broadcast sends content to a third party | Privacy Mode recommended for every shared destination; owner-only setup |
| Public app listing exposes usage by default | Documented; `hidden` visibility and `off` switch; default only shares token counts per model under the project's app, as OpenRouter's rankings do for any attributed app |
| Double counting when Broadcast and local both feed Grafana | Distinct `service.name` (`openrouter` vs `open-computer`); dashboards label the source |
| Obs code slows or breaks a run | Failure isolation + a unit test that proves a raising sink does not fail `run_once` |

## Sequencing

Stage 1 → Stage 2 need no operator decision and ship back to back. Stage 3 needs decision 1, Stage 4 decision 2
(plus owner action in the OpenRouter dashboard). Stage 5 can start on local data right after Stage 1. Stage 6 is
independent and only starts when the maintainer schedules it, after Stage 1 (it needs the call store) and the
Stage 4 reconcile loop (it needs trustworthy request counts).
