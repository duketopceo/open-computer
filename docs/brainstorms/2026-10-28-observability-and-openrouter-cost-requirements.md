---
title: Observability and OpenRouter cost data — requirements
type: brainstorm
date: 2026-10-28
status: draft for owner decisions
next: docs/plans/2026-10-28-001-feat-observability-and-cost-telemetry-plan.md
---

# Brainstorm: hefty dev/debug logging, and OpenRouter cost data we can trust

Maintainer request, 2026-10-28: *"pretty hefty dev/debug log and build … high high attention to openrouter
log/cost data … export log data to grafana and such from openrouter and maybe loop it back to see per chat and
BI on it."*

## Verdict

**Worth doing now, and the cost half is overdue.** The headline product promise is a hard cost cap. Today that
promise is not backed by measured data in the live path (see "What is true today"). Logging is the cheaper,
broader half; cost capture is the half that protects the cap.

## What is true today (verified against `main` @ 8ec7e25)

| Area | State |
|---|---|
| Logging | No `logging` module use anywhere in `services/` or `oc/`. 8 bare `print()` calls (opsview, relay). No levels, no debug mode, no correlation ids, no redaction. |
| Durable records | Three NDJSON sinks already exist and are good: cost ledger (`services/cost/ledger.py`), orchestrator audit (`orchestrator.ndjson`), goal event stream (`oc/events`). None carries a trace id; they join only by `run` id. |
| OpenRouter calls | Four call sites: `services/orchestrator/engines.py::_chat` (planner + cloud worker + sandbox script writer), `services/router/decide.py::ask_decisions`, and two in `bench/`. |
| **Cloud worker cost** | `_chat` returns `tokens / 1e6 * 0.30`, a flat guess, and **discards the provider's `usage.cost`**. `work_fn` returns plain text, so `run_once` never receives a `WorkResult` and **`CostLedger.record()` is never called in the real path**. The cap enforces on estimates only; actual spend is invisible. (`ask_decisions` does read `usage.cost`, but only into the router ledger.) |
| Attribution | No chat concept. A run has `run` and `subtask`; OpenRouter is never told which. Nothing is sent as `session_id`, `user`, or `trace`, so OpenRouter-side data cannot be joined back. |
| Console | `services/opsview/views.py` shows per-subtask cost from the event stream and a day total. No per-chat, per-model, per-provider view. |
| Export | `services/telemetry.py` ships failures to a Sentry-compatible DSN and an optional HTTP intake (both env-gated). Nothing ships logs, metrics or traces. |

## What OpenRouter gives us (checked against current docs, 2026-10-28)

1. **Response `usage` is always on.** Every chat response carries `usage.cost` (what OpenRouter charged),
   token counts, cached/reasoning/cache-write token breakdowns. `usage: {include: true}` is deprecated and does nothing.
   The response `id` is the generation id (`gen-…`). This is free, synchronous, and the right primary source.
2. **Generation stats API** — `GET /api/v1/generation?id=gen-…`. Adds what the response lacks: `latency`,
   `generation_time`, `provider_name`, `finish_reason`, `native_tokens_*`, `cancelled`, `is_byok`,
   `upstream_inference_cost` (BYOK only), `session_id`, `external_user`, `router`. Eventually consistent, so it
   can 404 right after a call. Use it to **reconcile**, with retry, never in the hot path.
3. **Request-side attribution fields.** `session_id` (<=256 chars, or `x-session-id` header), `user` (<=128),
   and a free-form `trace` object (`trace_id`, `trace_name`, `span_name`, `generation_name`, `parent_span_id`,
   `environment`, plus any custom keys). These flow to every Broadcast destination.
4. **Broadcast** (Settings > Observability) pushes one trace per request, asynchronously, to: Arize AX,
   Braintrust, ClickHouse, Comet Opik, Datadog, Elastic, BigQuery, **Grafana Cloud (Tempo, OTLP HTTP/JSON)**,
   Hugging Face buckets, **Langfuse**, LangSmith, New Relic, **OpenTelemetry Collector**, PostHog, Raindrop,
   Ramp, **S3/S3-compatible (R2 works)**, Sentry, Snowflake, W&B Weave, Webhook. Traces carry model, provider,
   tokens (with cache/reasoning split), input/output/total cost, TTFT and inter-token latency, status, finish
   reason, session and user, and our `trace` metadata as span attributes. Per destination: API-key filter,
   sampling rate (deterministic by `session_id`), and **Privacy Mode** (drops prompt/completion, keeps
   everything else). Optional "Cost" and "Cost allocation (beta)" metadata add `usage_is_estimated`, BYOK
   figures, cache-write TTL split, per-token-kind cost.
   - Broadcast is **traces only**. It does not send metrics or logs, and not to Prometheus or Loki directly.
     Prometheus-style metrics have to be derived (spanmetrics in Tempo / the collector) or computed by us.

## Requirements

**R1. One id spine.** Every log line, ledger row, audit row, event, and OpenRouter request carries the same
set: `trace_id` (32-hex, W3C-compatible), `run_id`, `goal`/`subtask`, `chat_id`, `service`. If a number is on
the console, one query reaches every log line and every OpenRouter generation behind it.

**R2. Hefty but safe logging.** Structured JSON, levels, a switch for debug mode (`OC_DEBUG=1` or per-service
level), file + stderr sinks with rotation. Secrets and PII are redacted **at the logger**, not by call-site
discipline. Prompt and completion text is **off by default** and opt-in per environment.

**R3. Every OpenRouter request leaves a complete local record.** Model requested vs served, provider, tokens
in/out/cached/reasoning, cost, latency, finish reason, status/error, generation id, and the ids from R1. Stored
on this box, append-only, queryable without the network. Provider-reported cost is the truth; the flat $0.30
estimate is removed from the accounting path (kept only as a *pre-call reservation*).

**R4. The cap sees real spend.** Cloud worker usage reaches `CostLedger.record()` so `paused_cap` reacts to
actual dollars.

**R5. Reconciliation.** A job re-fetches each generation from the stats API and marks the local row
`reconciled`, `mismatch` (cost/tokens differ), or `missing`. A mismatch is a bug report, not a silent fix.

**R6. Export to Grafana, vendor-neutral.** OTLP for traces and logs; Prometheus-format metrics for cost/token/
latency. A single `docker compose` stack (Grafana, Tempo, Loki, Prometheus, collector) works on the box; Grafana
Cloud works by changing three env vars. No call-site changes between the two.

**R7. OpenRouter-side export is additive, not the system of record.** Broadcast feeds Grafana/analytics so we
see OpenRouter's own view (including requests made outside this repo with the same key). Local records stay the
source for caps and per-chat views.

**R8. Loop back into the opsview console.** Per-chat, per-run, per-goal, per-model, per-provider cost and
latency views; budget burn; anomalies (cost spike, retry storm, `usage_is_estimated`). Plus a BI export
(CSV/Parquet) so any operator can query it from their own tools.

**R10. Attribution on every call.** Every OpenRouter request, from any call site (planner, worker, sandbox
script writer, router decisions, bench), carries app attribution headers and, where OpenRouter supports it,
`session_id` (chat), `user` (opaque, opt-in) and `trace` metadata. Configurable or switchable off; no operator-specific defaults.

**R9. Never break the product.** Logging and export failures never fail a run (same rule as `services/telemetry.py`).
Disk full or collector down degrades to stderr and a counter. Logging off costs ~nothing.

## Non-goals

- Anything specific to one operator's machines, domains or accounts (see "Open source, so everything is configurable").

- Replacing the cost ledger, audit log, or event stream. They stay authoritative for their jobs; this work
  joins them.
- A hosted SaaS or multi-tenant telemetry. Single owner, own machines.
- Capturing prompt content in shared/hosted destinations by default.
- Streaming (SSE) calls: nothing in the repo streams to OpenRouter today. Design for it, build it when needed.

## Approaches considered

| | A. Local-first, then export (recommended) | B. Broadcast-only | C. Adopt Langfuse/LangSmith as the store |
|---|---|---|---|
| Source of truth | local NDJSON + reconcile | OpenRouter's trace | their DB |
| Cap sees real spend | **yes**, synchronously | no (async, after the fact) | no |
| Works offline / when OR dashboard changes | yes | no | partly |
| Grafana | via OTel exporter we own | direct, traces only | extra hop |
| Cost to build | medium | near zero | medium + new service to run |
| Covers non-OpenRouter calls (local llama.cpp, Groq STT) | yes | no | partly |

**Counter-argument to A (steelmanned):** "Broadcast already does this with zero code." True for *viewing*
traces. It cannot enforce the cap, cannot see local-model calls, arrives late, carries no `chat_id` unless we
send it anyway, and ties our accounting to a vendor feature that has already changed shape several times
(deprecated `usage.include`, new `cost allocation (beta)`). So we send the attribution fields regardless
(cheap) and turn Broadcast on **as a second view**, not as the record.

**What would change this verdict:** if local-model coverage does not matter to the operator and the cap is
estimate-only forever, B is enough. If per-prompt eval/debugging becomes the main need, add Langfuse as a
Broadcast destination (still not the record).

## Open source, so everything is configurable

This is a public repo for self-hosters. Rules for every stage:

- **No personal infrastructure in code or defaults.** No hostnames, tunnels, keys, accounts or machine names.
  Everything is an `OC_*` env var (or a config file path from one) with a default that works on a fresh laptop:
  local files under `OC_OBS_DIR` (default `~/.local/share/oc/obs`, same convention as the other state dirs),
  stderr logs, exporters **off**.
- **Exporters are opt-in and standard.** OTLP endpoints use the standard `OTEL_EXPORTER_OTLP_*` variables. The
  Grafana stack is a sample under `deploy/obs/` that operators copy; pointing at Grafana Cloud or any other OTLP
  backend is a variable change.
- **Docs are for strangers.** Runbooks name steps and variables, never one operator's setup.

## Attribution: what is actually possible (verified against OpenRouter docs, 2026-10-28)

| Mechanism | What we send | What it enables | Limits |
|---|---|---|---|
| **App attribution headers** | `HTTP-Referer` (required, the app's identity), `X-OpenRouter-Title` (`X-Title` still accepted), optional `X-OpenRouter-Categories` (<=2, from a fixed list), optional `X-OpenRouter-App-Visibility: hidden` | The app gets its own page and per-app analytics (models over time, tokens); usage can be **filtered/grouped by app** in Activity and in the analytics query API (`app` dimension, filtered by numeric app id); public apps appear in rankings and on model pages | Without `HTTP-Referer` no app exists (title alone does nothing; localhost referers also need a title). `hidden` only applies when the request *creates* the app; it cannot be changed later by request. The filter uses the numeric app id, not the title. |
| **`user` field** (<=128 chars) | opaque, hashed operator id (never an email) | `external_user` on the generation, a filterable dimension; `userId` in Broadcast traces | Identifies an end user to a third party, so it is opt-in and hashed |
| **`session_id` field or `x-session-id` header** (<=256) | `chat_id` | Groups generations into a conversation; shown on the generation, filterable in logs, `sessionId` in Broadcast; makes Broadcast sampling consistent per session | One session per request |
| **`trace` object** | `trace_id`, `trace_name`, `span_name`, `generation_name`, `parent_span_id`, `environment`, plus our own keys (`oc_run`, `oc_subtask`, `oc_chat`) | Passed through to **every** Broadcast destination as span attributes; `trace_id`/`parent_span_id` nest OpenRouter's span under ours in Tempo/Langfuse/etc. | Free-form keys are metadata only: they are not analytics dimensions in Activity |
| **Analytics API** (`POST /api/v1/analytics/query`, Management key) | n/a (read side) | Programmatic Explore: cost/tokens/requests by `app`, `model`, `provider`, `api_key_id`, `external_user`, daily or finer. This is how the console can show OpenRouter's own numbers next to ours | Needs a *management* key, separate from the inference key; optional feature |
| **Broadcast** | n/a (dashboard setup) | Per-request traces to the destinations listed above; per-destination API-key filter and sampling | Configured in the OpenRouter dashboard by the account owner; cannot be done from the repo. Traces only (no metrics/logs). |

Not possible: filtering Activity by our free-form `trace` keys, or by run/chat, inside OpenRouter. Per-chat and
per-run views therefore have to come from our own call store (and from Broadcast traces in the operator's
backend, where the `trace` keys are queryable attributes).

## Decisions for the operator (defaults chosen so nothing blocks)

1. **Where Grafana runs.** Self-hosted sample stack (default, nothing sent anywhere), Grafana Cloud, or any OTLP
   backend. *Default: sample compose stack, exporters off until `OTEL_EXPORTER_OTLP_ENDPOINT` is set.*
2. **Broadcast destination** (optional). Self-hosted OTel Collector (needs an HTTPS endpoint reachable from the
   internet), Grafana Cloud (no inbound port), S3/S3-compatible bucket (cheap BI archive), Langfuse (prompt
   debugging). *Recommendation: Grafana Cloud or a bucket first. Owner action in the OpenRouter dashboard.*
3. **Prompt content retention.** Metadata-only everywhere (default), full content to local disk in debug mode
   only (`OC_OBS_CAPTURE_CONTENT=1`), or full content to a Broadcast destination with Privacy Mode off.
4. **What is a "chat"?** There is no chat object today. Proposal: `chat_id` = console conversation/session if
   one exists, else the goal id, else the run id. Overridable per submit.
5. **Retention.** Defaults: 14 days debug logs, 90 days call records, cost-ledger rows forever. All env-tunable.
6. **Alert channel** for cost anomalies: console banner by default; webhook URL optional.
7. **App attribution identity.** Default `HTTP-Referer` = this project's public URL and title `Open Computer`, so
   every self-hoster's usage rolls up under one recognizable app. Operators who do not want that set their own
   referer/title, `OC_OPENROUTER_APP_VISIBILITY=hidden`, or `OC_OPENROUTER_ATTRIBUTION=off`.

## Success criteria

- For any dollar on the console, we can list the OpenRouter generations behind it in one query.
- Zero silent spend: sum of call records equals `CostLedger` spend per run, and both match OpenRouter's
  dashboard to within reconciliation tolerance.
- A failing run can be diagnosed from `OC_DEBUG=1` logs by `trace_id` alone.
- No secret or API key string appears in any log line (test-enforced).
