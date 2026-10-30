---
title: Observability and OpenRouter cost telemetry — staged plan
type: feat
date: 2026-10-28
origin: docs/brainstorms/2026-10-28-observability-and-openrouter-cost-requirements.md
revised: 2026-10-28
status: Stages 1 and 2 built (see docs/runbooks/observability.md). Stage 3 replanned around an optional Postgres/Supabase sink (not built). Stages 4-5 follow it. Stage 6 is a later side track, not scheduled
---

# Observability and OpenRouter cost telemetry

## Summary

Six stages. Each ships alone, is green in CI, and is useful without the next. Stage 1 fixes the defect that
matters most (real spend never reaches the cost ledger) and lays the id spine and attribution every later stage
rides on. Stage 2 widens logging. Stage 3 adds an **optional Postgres/Supabase sink** that copies OpenRouter usage
and cost data into a database the operator owns. Stage 4 reconciles with OpenRouter. Stage 5 builds the per-chat
cost and BI views on top of that store. Stage 6 is a separate side track (free-model router harness) and is **not**
part of the current build.

**Revision 2026-10-28.** Stage 3 originally exported to a Grafana stack over OTLP and Prometheus. The maintainer
does not use Grafana, BigQuery or Datadog and wants the data in Supabase, so Stage 3 is replanned as a generic,
off-by-default Postgres sink that works on any Postgres and is tuned for Supabase. Stage 4 loses its Grafana-first
recipes and Stage 5 reads from the new store. The OTLP and Prometheus work is kept only as a deferred, unscheduled
option (see Scope Boundaries).

Requirement ids (R1-R10) are from the origin brainstorm.

## Design decisions (apply to every stage)

- **General, configurable, no personal setup.** This is an open-source project. Nothing in code, defaults,
  tests, sample configs or docs may name one operator's hosts, domains, tunnels, keys or machines. Config is
  `OC_*` env vars with defaults that work on a fresh checkout: local files
  under `OC_OBS_DIR` (default `~/.local/share/oc/obs`), stderr logs, every sink off. A lint test greps the
  tree for a deny-list of operator-specific strings. Docs are written for strangers.
- **Stdlib first.** Core of `oc/obs` uses `logging`, `contextvars`, `json`, `urllib`. The repo has one runtime
  dependency (`fido2`); we keep it that way. The Postgres driver arrives in Stage 3 as an **optional extra**
  (`[project.optional-dependencies] pgsink`), imported lazily and only when the sink is configured; absent = the
  sink reports "driver missing" and nothing else changes. Same pattern as `space`.
- **Local NDJSON is the record; sinks are copies.** Mirrors the ledger/audit/event-stream design already in
  the repo. Append-only, `flock` for writers, torn-last-line tolerant readers. A database sink tails these files
  and can be lost or rebuilt without touching a run or the cost cap.
- **One id spine** (R1): `trace_id` (32 hex, derived deterministically from `run_id` via sha256 so a restarted
  process, `Orchestrator.recover`, and durable activities land on the same trace with no new state), `run_id`,
  `subtask`, `chat_id`, `service`. Carried by `contextvars` in-process and by explicit fields on the fsproto
  task body across the worker boundary (the worker may be another process).
- **Redaction at the sink** (R2). One `redact()` runs on every record before serialization: key-name rules
  (`*key*`, `*token*`, `*secret*`, `authorization`, `password`, `cookie`), value-pattern rules (OpenRouter and
  other `sk-…` keys, `Bearer …`, JWT shape, `gh*_…` tokens, AWS ids, emails, phone numbers), plus exact-match
  scrubbing of the *values* of any env var whose name looks secret. Prompt/completion text is never logged **or
  sent to any sink** unless `OC_OBS_CAPTURE_CONTENT=1`, and then still redacted.
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
  parent_span_id (reserved, unset), environment, oc_run, oc_subtask, oc_chat}`.
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

### Stage 3 — Optional Postgres/Supabase sink for OpenRouter usage and cost  (R6, revised)  — decision 1 resolved

> **Revision 2026-10-28.** This stage originally exported OTLP traces/logs and Prometheus metrics to a sample Grafana
> stack. The maintainer does not use Grafana, BigQuery or Datadog and wants the OpenRouter usage and cost data in
> Supabase. Stage 3 is replanned around a generic Postgres sink. The OTLP/Prometheus work is not dropped from the
> project's possibilities; it moves to *Deferred to Follow-Up Work* below, unscheduled.

**Goal:** an operator who sets one environment variable gets every OpenRouter call, every run and every chat
copied, in near real time, into a Postgres database they own, with a schema, migrations, retention and access
rules that work on any Postgres and are tuned for Supabase. An operator who sets nothing gets exactly what Stages
1 and 2 already give them, with no new import, thread, socket or file.

#### Decisions (see origin: `docs/brainstorms/2026-10-28-observability-and-openrouter-cost-requirements.md`, R6, R8, R9)

- **Off by default, local-first.** The sink is enabled only when `OC_OBS_SINK_POSTGRES_URL` (or
  `OC_OBS_SINK_POSTGRES_URL_FILE`) is set. Unset means the sink module is never imported and the database driver is
  never loaded. The local NDJSON files stay the system of record, and the cost cap keeps enforcing from the local
  ledger. The database is a downstream copy that can be lost, rebuilt or pointed elsewhere without touching a run.
- **The outbox is the existing NDJSON files.** The hot path (`services/openrouter/client.py`, the ledger, the
  orchestrator audit) is **not changed** and does not know the sink exists. A separate **shipper** process tails the
  files, remembers its position, and writes batches to Postgres. A dead or slow database therefore cannot slow or
  fail a run, which is R9 by construction rather than by exception handling. Alternative considered and rejected:
  writing to Postgres inline from `CallStore.append` (adds network latency and a failure mode to every model call,
  and needs its own queue to survive an outage, which is exactly what the NDJSON file already is).
- **Any Postgres, tuned for Supabase.** One driver (`psycopg` 3, optional extra `pgsink`), one connection URL. No
  Supabase client library, REST API or API keys: the shipper speaks the Postgres protocol with a least-privilege
  role. Supabase-specific behavior (role names, Data API exposure, pooler modes) is handled in the migrations and
  the runbook, guarded so the same files apply cleanly to plain Postgres.
- **Delivery is at-least-once with idempotent writes**, which is effectively once. Every row has a natural key; every
  insert is `ON CONFLICT DO NOTHING` or a deterministic upsert; the position advances only after commit. A crash
  between commit and position write replays a batch as a no-op.
- **Content is a separate table behind one switch.** Prompt text, completion text and provider error text go to
  `call_content`, and only when `OC_OBS_CAPTURE_CONTENT=1` is set **when the row is shipped** as well as when it was
  captured. Turning the switch off later stops uploads of content that is still sitting in the local file. The
  shipper strips content fields at the boundary (`oc/obs/sink/rows.py`) rather than trusting the file. No second
  flag exists in v1 (see Open Questions).
- **Metadata that ships:** ids, model names, provider, token counts, cost, latency, finish reason, status, error
  class and HTTP status, prompt length and the truncated prompt hash already in the call row. **Not shipped:** the
  request URL host, any header, the hashed `user` value, and any field the redactor masked.
- **The connection URL is a secret.** It is never logged (the redactor already masks `user:password@` URLs and
  secret-named keys), never written to a status file, and may be supplied as a file path so it stays out of the
  process environment.

#### Configuration

All variables are optional except the URL. Defaults work on a fresh checkout.

| Variable | Default | Effect |
|---|---|---|
| `OC_OBS_SINK_POSTGRES_URL` | unset (sink off) | libpq-style URL for the **writer** role. Setting it is the opt-in |
| `OC_OBS_SINK_POSTGRES_URL_FILE` | unset | path to a file holding the URL; used when the URL variable is unset |
| `OC_OBS_SINK_MIGRATE_URL` | unset | optional owner-role URL used only by `sink migrate`; falls back to the writer URL |
| `OC_OBS_SINK_SCHEMA` | `oc_obs` | schema that holds every object; never `public` |
| `OC_OBS_SINK_INSTANCE` | generated once, stored beside the position file | label for this installation, so several installations can share one database |
| `OC_OBS_SINK_BATCH_ROWS` / `OC_OBS_SINK_BATCH_SECONDS` | `200` / `5` | flush when either is reached |
| `OC_OBS_SINK_CONNECT_TIMEOUT` / `OC_OBS_SINK_STATEMENT_TIMEOUT` | `5s` / `15s` | bounds on any single database wait |
| `OC_OBS_SINK_MAX_BACKOFF_SECONDS` | `300` | ceiling for retry delay |
| `OC_OBS_SINK_AUTO_MIGRATE` | `0` | `1` lets `run` apply pending migrations; the default is an explicit `migrate` command |
| `OC_OBS_SINK_ALLOW_INSECURE` | `0` | `1` permits `sslmode=disable` or `prefer` to a non-loopback host; the shipper refuses by default |
| `OC_OBS_SINK_PRUNE` | `0` | `1` lets `run` prune once a day; otherwise prune is an explicit command or a database-side schedule |
| `OC_OBS_RETENTION_DAYS_CALLS` | `90` | `calls` rows older than this are deleted after being folded into `cost_daily` |
| `OC_OBS_RETENTION_DAYS_CONTENT` | `14` | `call_content` rows older than this are deleted |
| `OC_OBS_RETENTION_DAYS_RUNS` | `0` (keep) | `runs` rows older than this are deleted; `chats` and `cost_daily` are kept |

`0` means "keep forever" for every retention variable.

#### How it works

```mermaid
flowchart LR
  subgraph box["Installation (unchanged hot path)"]
    C["OpenRouter client"] --> CS[("openrouter-calls.ndjson")]
    L["Cost ledger"] --> LF[("ledger NDJSON")]
    O["Orchestrator audit"] --> OF[("orchestrator.ndjson")]
  end
  CS --> SH
  LF --> SH
  OF --> SH
  SH["Shipper<br/>python -m oc.obs sink run"] -->|"batch, one transaction,<br/>idempotent"| PG[("Postgres / Supabase<br/>schema oc_obs")]
  SH --> POS[("position file<br/>per source")]
  SH --> REJ[("rejected rows file")]
  PG --> V["views, read-only role"] --> S5["Stage 5: console + BI tools"]
```

The shipper has three states. It reports the current one in `sink status` and logs only **transitions**, never
every retry.

```mermaid
stateDiagram-v2
  [*] --> shipping
  shipping --> backoff: connect/timeout/transient error
  backoff --> shipping: next attempt succeeds
  backoff --> backoff: attempt fails, delay doubles with jitter up to the ceiling
  shipping --> blocked: auth, missing schema, missing privilege, migration needed
  blocked --> shipping: operator fixes it, next probe succeeds
  blocked --> blocked: probe fails, delay at the ceiling
```

- **Sources.** Three tailers, each with its own position (byte offset plus file identity, so a replaced or truncated
  file restarts from the top and a torn last line is left for the next pass): the call store (`call` and
  `reconcile` rows), the cost ledger (`cost`, `cap_set`, `run_paused`, `run_resumed`), and the orchestrator audit
  (`run_submit`, `run_failed`, settle). Positions live under `OC_OBS_DIR` in a small JSON file per source, written
  atomically (temp file, fsync, rename). One shipper per installation, enforced with a local `flock`, not a database
  advisory lock, because session-level locks do not survive Supabase's transaction pooler.
- **Batching.** Rows accumulate to `OC_OBS_SINK_BATCH_ROWS` or `OC_OBS_SINK_BATCH_SECONDS`, whichever comes first,
  and are written in one transaction as multi-row statements. No prepared statements, pipelining or session state,
  so the same code works on a direct connection, a session pooler and a transaction pooler.
- **Retries.** Transient errors (connection refused or reset, timeout, `57P01` admin shutdown, serialization
  failure, `53xxx` resource limits) retry the same batch with exponential backoff, full jitter, base 1 s, ceiling
  `OC_OBS_SINK_MAX_BACKOFF_SECONDS`. The batch is never dropped and the position never advances on failure.
- **Blocked errors** (`28xxx` authentication, `3D000` no database, `42P01` missing table, `42501` missing
  privilege, schema older than the code needs) are not transient. The shipper stops sending, probes at the backoff
  ceiling, and says exactly what is wrong in `sink status` and in one ERROR log line. It never creates or alters
  anything on its own unless `OC_OBS_SINK_AUTO_MIGRATE=1`.
- **Poison rows.** A row that cannot be mapped (wrong type, absurd value) or that the database rejects with a data
  error (`22xxx`, `23xxx` other than the expected duplicate) is isolated by splitting the failed batch in half until
  the offender is found, written with its reason to `sink-postgres.rejected.ndjson` under `OC_OBS_DIR`, counted,
  and skipped. The rejected file stores the row **after** redaction and **without** content fields.
- **When the database is unreachable.** Runs, caps, logs and the local call record are unaffected: nothing on the
  hot path calls the sink. The local files keep growing and are the backlog. When the database returns, the
  shipper drains the backlog in order at full batch size. `python -m oc.obs sink status` shows state, last error
  class (never the URL or password), rows and bytes behind, age of the oldest unshipped row, rows rejected, and
  last successful commit. The shipper crash-restarts under a supervisor (sample unit below) and resumes from its
  position. If the local files are pruned or rotated before the shipper catches up, those rows never reach the
  database; local rotation therefore must be cursor-aware (see S3-U5), and the status output flags any gap.
- **Hosted databases.** Free or small hosted tiers can cap storage or pause an idle project. Both look like
  "unreachable" or "blocked" to the shipper and are handled by the same states; the runbook tells operators to
  check current limits for their provider and to size retention to them.

#### Schema

Everything lives in one schema (`oc_obs` by default), created by migrations. Names below are the contract; exact
column types and index definitions are settled in S3-U1. Money is `numeric`, never float. Times are `timestamptz`.

```mermaid
erDiagram
  instances ||--o{ chats : has
  instances ||--o{ runs : has
  chats ||--o{ runs : groups
  runs ||--o{ calls : contains
  calls ||--o| call_content : "optional, content switch"
  instances ||--o{ cost_daily : rolls_up
```

| Table | Key | Purpose and notable columns |
|---|---|---|
| `schema_migrations` | `version` | applied migrations, checksum, applied time. A changed checksum on an applied version stops the migrator |
| `instances` | `instance_id` | label, first and last seen, project version. Lets several installations share one database |
| `chats` | `instance_id, chat_id` | first and last seen, `source` (`console`, `goal`, `run`: how the id was derived), optional operator-supplied label. Aggregates come from a view, not stored here, so they cannot drift |
| `runs` | `instance_id, run_id` | `trace_id`, `chat_id`, goal id, state (`running`, `paused_cap`, `done`, `failed`), `cap_usd`, `ledger_spend_usd` (from the cost ledger), submit and settle times. Upserted from ledger and audit rows |
| `calls` | `instance_id, call_id` | one row per OpenRouter request: `ts`, `kind`, `run_id`, `subtask`, `task_id`, `chat_id`, `trace_id`, `service`, `model_requested`, `model_served`, `provider`, `generation_id`, `status`, `error_class`, `http_status`, `finish_reason`, tokens in/out/cached/reasoning, `cost_usd`, `cost_source`, `upstream_cost_usd`, `is_byok`, `latency_ms`, `prompt_chars`, `prompt_sha256`, `reconcile_status`, `reconciled_at`, `reconcile_detail`, `extra`, `ingested_at` |
| `call_content` | `instance_id, call_id` | `prompt`, `completion`, `error_text`, `captured_at`. Cascade-deleted with its call. Written only under the content switch |
| `cost_daily` | `instance_id, day, model_served, provider, kind` | call count, error count, tokens, cost. Frozen from `calls` before retention deletes them, so long-range BI survives pruning |

- `extra` (jsonb) holds unknown, non-content keys from a call row so a newer client talking to an older schema, or
  an OpenRouter response-shape change, does not drop data or fail a batch. Content keys are never copied there.
- `ingested_at` defaults to the database clock; `ts` is the installation's clock. Both are kept so clock skew is
  visible instead of silently reordering history.
- A reconcile row updates `reconcile_*` on its call. If no call matches (not shipped yet, or already pruned) it is
  counted at DEBUG and skipped; ordering inside one source file means a call always precedes its reconcile rows.
- **Indexes** (start small, add on evidence): `calls (instance_id, ts desc)`, `(instance_id, chat_id, ts)`,
  `(instance_id, run_id)`, `(generation_id)`, and a partial index on `calls` where `reconcile_status` is not
  `reconciled`. Partitioning is deferred until a real volume demands it.
- **Views** (the only objects the read role may touch), all created `WITH (security_invoker = true)` so the
  invoker's row-level rules apply rather than the owner's: `v_chat_cost`, `v_run_cost` (also compares call-record
  spend with ledger spend per run, the origin's "zero silent spend" check), `v_model_cost_daily` (live `calls`
  plus frozen `cost_daily`), `v_unreconciled`, `v_cost_outliers` (feeds Stage 5 anomalies).
- **Migrations are forward-only and additive** within a major version: add tables, columns and indexes; never
  rename or drop. Files are numbered (`0001_…sql`), each guarded so it can re-run, applied in one transaction under
  a transaction-scoped advisory lock. `sink migrate` applies them, `sink migrate --print` prints the SQL for
  operators who apply it through their own tooling (for example a Supabase migration workflow or the SQL editor),
  and `sink migrate --check` exits non-zero when the database is behind. A database *newer* than the code warns
  and continues, which is safe because changes are additive.

#### Access control and Supabase row-level security

- **Keep the schema off the Data API.** Supabase exposes `public` through its REST and GraphQL APIs by default and
  exposes any other schema only after it is added to "Exposed schemas". The sink needs neither, so `oc_obs` is
  **never** exposed. The runbook says so in the first paragraph and shows how to verify it.
- **Row-level security on every table anyway.** Migrations `ENABLE ROW LEVEL SECURITY` on all tables so that if an
  operator later exposes the schema by mistake, the default is deny rather than world-readable. A role without a
  matching policy sees zero rows.
- **Revoke from Supabase's public-facing roles.** Where the roles `anon` and `authenticated` exist, migrations
  revoke all privileges on the schema from them. On plain Postgres those roles do not exist, so the revoke is
  conditional and a no-op.
- **Two dedicated login roles, no superuser.** `oc_obs_writer` can insert, update and (for retention) delete on the
  tables; `oc_obs_reader` can select on the views only, and **cannot select `call_content`** unless the operator
  grants it deliberately. Because neither role bypasses row-level security, the migrations add explicit policies for
  them (writer: all operations on all tables; reader: select on the tables behind the views). The migrations never
  create login roles or set passwords; the runbook gives a template the operator runs with their own password.
- **Do not use the project's elevated credentials.** The sink must not be given the `postgres` role, a Supabase
  secret or `service_role` key, or any role with `BYPASSRLS`. Those bypass every policy above, and a policy that
  names `service_role` does nothing because that role never evaluates policies. The runbook states this plainly.
- **Views and RLS.** A view runs with its owner's rights unless it is created with `security_invoker`; without it a
  view silently bypasses row-level security, a common Supabase mistake. A test asserts every view has it.
- **Transport.** Supabase offers a direct connection, a session pooler (port 5432, works from IPv4-only hosts) and a
  transaction pooler (port 6543, no prepared statements or session state). The shipper is long-lived and works on
  all three; the runbook recommends direct or session mode and notes the pooler constraints the code already
  respects. TLS is required for any non-loopback host (`OC_OBS_SINK_ALLOW_INSECURE=1` overrides).
- **Optional exposure through Supabase Auth** (letting signed-in users read their own rows over the Data API) is
  out of scope: the data model is single-owner. The runbook may include a commented policy template keyed on a JWT
  claim for operators who want it, clearly marked unsupported.

#### Retention

`python -m oc.obs sink prune` (also run daily by `sink run` when `OC_OBS_SINK_PRUNE=1`; a `pg_cron` recipe is in the
runbook for operators who prefer the database to do it) deletes in small batches, oldest first. For each whole UTC
day older than the calls cutoff it first recomputes that day's `cost_daily` rows from `calls` and upserts them, then
deletes the day's calls, in one transaction. The recompute is idempotent, so a crash mid-prune loses nothing, and
partial days are never deleted. `--dry-run` prints counts only. Content expires on its own, shorter, clock.

#### Implementation units

#### S3-U1. Schema, migrations and roles

**Goal:** the database objects, applied safely by an explicit command, on plain Postgres and on Supabase.
**Requirements:** R6 (revised), R8, R9; success criterion "one query from any dollar to its generations".
**Dependencies:** none.
**Files:** `oc/obs/sink/migrations/0001_init.sql`, `oc/obs/sink/migrations/0002_views_and_grants.sql`,
`oc/obs/sink/migrate.py`, `docs/runbooks/observability-postgres.md`, `.github/workflows/ci.yml` (a job with a Postgres service container),
`tests/unit/test_obs_sink_migrate.py`,
`tests/unit/test_obs_sink_postgres_integration.py`.
**Approach:** tables, indexes, views and grants as specified above; conditional blocks for roles that may not exist;
checksum table; `migrate`, `migrate --print`, `migrate --check`. Migration files contain no passwords, hostnames or
project references.
**Execution note:** write the integration test (against a real Postgres) first; it defines done for roles and RLS. It is skipped unless `OC_TEST_POSTGRES_URL` points at a throwaway database, and CI sets it from a pinned Postgres service container.
**Test scenarios:**
- Fresh database: `migrate` creates every table and view; running it twice changes nothing.
- A role with no grants selects from every table and gets zero rows or a permission error; the writer role can
  insert; the reader role can select a view and is refused `call_content`.
- Every view reports `security_invoker` on; a view queried by the reader respects the reader's policies.
- Database without `anon`/`authenticated` roles: migrations still apply. Database with them: both are revoked.
- Edited checksum on an applied migration: `migrate` refuses and names the version. `--check` exits non-zero when
  behind and zero when current. `--print` output contains the same statements and no connection string.
- Schema name override (`OC_OBS_SINK_SCHEMA`) is honored everywhere, including quoting of unusual names.
**Verification:** the integration suite passes against Postgres 15 and the newest supported major.

#### S3-U2. Row mapping, redaction and the content gate

**Goal:** a pure function from a local NDJSON row to table rows, with no database in the loop.
**Requirements:** R2, R3; "never store prompt content unless `OC_OBS_CAPTURE_CONTENT=1`".
**Dependencies:** none.
**Files:** `oc/obs/sink/rows.py`, `tests/unit/test_obs_sink_rows.py`.
**Approach:** whitelist the metadata columns; unknown non-content keys to `extra`; `prompt`, `completion`, `error`
(call text) routed to `call_content` only when the switch is on at ship time; run redaction again at this boundary;
reject rather than coerce on type errors.
**Test scenarios:**
- A call row with seeded prompt, completion and error text and the switch **off** produces no `call_content` row
  and the seeded strings appear in no output value, including `extra` and the rejected-file record.
- Same row with the switch **on** produces a `call_content` row, still redacted (seeded key and email masked).
- Unknown key `new_field` lands in `extra`; unknown key named `prompt_v2` or `completion_x` does not.
- Missing cost, `cost_source: none`, error row, zero tokens, and a reconcile row each map correctly.
- Oversized string fields are truncated with a marker; a non-numeric cost is rejected with a reason.
- A seeded `postgres://user:secret@host/db` string anywhere in a row never appears in output.
**Verification:** property-style test over generated rows: no output contains a seeded secret or content sentinel
when the switch is off.

#### S3-U3. Sources and positions

**Goal:** read new rows from the three files exactly once per pass, surviving torn lines, truncation and rotation.
**Requirements:** R9.
**Dependencies:** none.
**Files:** `oc/obs/sink/source.py`, `tests/unit/test_obs_sink_source.py`.
**Approach:** per-source reader keyed by file identity and offset; atomic position file; leave a torn trailing line
unread; report lag in rows and bytes.
**Test scenarios:**
- Appending while reading yields each row once; a torn last line is picked up complete on the next pass.
- File replaced by a new inode, or truncated: reader restarts at the top and reports the gap, not an error.
- Position file corrupted or missing: reader restarts from the top and relies on idempotent writes.
- Missing source file is "nothing to ship", not a failure. Two readers on one installation: the second refuses
  (local lock).
**Verification:** a kill-and-restart loop test never loses or duplicates a row.

#### S3-U4. Shipper, state machine and CLI

**Goal:** the loop that batches, retries and reports, behind `python -m oc.obs sink run|once|status|migrate|prune`.
**Requirements:** R6 (revised), R9.
**Dependencies:** S3-U1, S3-U2, S3-U3.
**Files:** `oc/obs/sink/ship.py`, `oc/obs/sink/__main__.py`, `oc/obs/__main__.py`, `pyproject.toml` (extra `pgsink`),
`uv.lock`, `tests/unit/test_obs_sink_ship.py`, `tests/unit/test_obs_sink_off_by_default.py`.
**Approach:** as in *How it works*. The driver is imported only inside the shipper and only when the URL is set.
Error classification by SQLSTATE class into transient, blocked, data. Status written to a small JSON file and
printed by `status`. A fake driver (in `tests/unit/`) drives the state machine without a database.
**Test scenarios:**
- URL unset: importing `oc.obs` and running a full fake run imports no driver module and opens no socket
  (assert on `sys.modules` and a socket guard).
- Driver not installed but URL set: `status` reports "driver missing" with the install hint; no crash, no effect on
  any other command.
- Connection refused for N attempts: delay grows with jitter to the ceiling, the position does not move, and after
  recovery all backlog rows arrive in order exactly once.
- Auth failure: state `blocked`, one ERROR line, no tight retry loop, the message contains no URL or password.
- One bad row in a batch of 200: it lands in the rejected file with a reason; the other 199 commit.
- Commit succeeds, position write fails (injected): next pass replays the batch with no duplicate rows.
- `status` output and every log line from a run with a seeded password in the URL contain no part of the password.
- `sslmode=disable` to a non-loopback host is refused unless `OC_OBS_SINK_ALLOW_INSECURE=1`.
- A full orchestrator run (and `run_once`) with the sink configured against an unreachable address succeeds, with
  cost accounting identical to a run with the sink off.
**Verification:** with a real Postgres, a seeded fake-OpenRouter run's `calls` totals equal the local report totals
(`python -m services.openrouter report --by run`).

#### S3-U5. Retention and cursor-aware local rotation

**Goal:** bounded storage in the database and, when a sink is configured, a local backlog that is never discarded
before it ships.
**Requirements:** R9; origin decision 5 (retention).
**Dependencies:** S3-U4.
**Files:** `oc/obs/sink/prune.py`, `services/openrouter/store.py` (rotation hook only), `tests/unit/test_obs_sink_prune.py`,
`tests/unit/test_obs_sink_postgres_integration.py`.
**Approach:** per-day fold then delete as described under *Retention*. Local call-file rotation, if enabled, only
removes data behind every sink position and logs an ERROR with a count if a hard size cap forces it to drop
unshipped rows.
**Test scenarios:**
- Fold-then-delete: totals in `v_model_cost_daily` are identical before and after a prune.
- Prune run twice, and interrupted between fold and delete, converges to the same state.
- A partial (current) day is never deleted. Retention `0` deletes nothing. `--dry-run` changes nothing.
- Content expires on its own shorter clock while its call row remains.
- Local rotation never removes a row ahead of the shipper position; the forced-drop path reports the lost count.
**Verification:** prune against a million-row synthetic table completes in bounded batches without a long lock.

#### S3-U6. Runbook, sample supervisor units and hygiene lint

**Goal:** a stranger can enable the sink in ten minutes and knows what it sends.
**Requirements:** "docs are for strangers"; no operator-specific strings.
**Dependencies:** S3-U1, S3-U4.
**Files:** `docs/runbooks/observability-postgres.md`, `docs/runbooks/observability.md` (link), `deploy/obs/oc-obs-sink.service`,
`deploy/obs/oc-obs-sink.timer`, `tests/unit/test_obs_open_source_hygiene.py`.
**Approach:** runbook covers: what is sent and what is not; quick start on any Postgres; Supabase specifics (keep the
schema off the Data API, roles, connection modes, TLS); the role template; migrate; run as a service or on a
timer; `status`; retention; BI read role; troubleshooting by state. Examples use placeholders such as
`<host>` and `<project-ref>` only. Hygiene lint grows file coverage to the new runbook, units and `oc/obs/sink/`,
and adds shape checks that fail on a populated connection string (a password between `:` and `@`) or a hosted
project hostname in any doc or sample file.
**Test scenarios:**
- Lint passes on the new files; a fixture containing a realistic connection string fails the lint.
- Every environment variable in the configuration table above appears in the runbook, and every variable in the
  runbook exists in code (single-source test).
**Verification:** a reader following only the runbook against a throwaway Postgres reaches `status: shipping`.

#### Stage 3 exit criteria

- With the variable unset, the full test suite and a fresh-checkout run behave exactly as Stage 2.
- With a Postgres configured, after a seeded run the database holds the same per-run and per-chat cost as the local
  report, with no content columns populated.
- Killing the database mid-run does not fail the run; restoring it drains the backlog without loss or duplicates.
- The reader role cannot read content, the schema is not on the Data API, and every table has row-level security
  enabled, all enforced by tests.

### Stage 4 — OpenRouter Broadcast + reconciliation loop  (R5, R7)  — operator decision 2

- Runbook `docs/runbooks/openrouter-broadcast.md`: Settings > Observability steps (account-owner action),
  destination recipes that need no particular vendor (a webhook receiver, an S3-compatible bucket, a self-hosted
  OpenTelemetry Collector, Langfuse for prompt debugging), Privacy Mode on for anything shared, sampling, and an
  API-key filter so only the key this app uses flows. Broadcast stays optional and additive: with the Stage 3 store,
  most operators do not need it.
- `reconcile` runs on a schedule the operator chooses (documented cron/systemd/timer examples): re-fetch
  unreconciled rows older than N minutes, write `reconciled|mismatch|missing`, console banner on mismatch or
  `usage_is_estimated`. The result is a `reconcile` row in the local call store, so the Stage 3 shipper copies it to
  `calls.reconcile_*` with no extra code path; `v_unreconciled` lists what is outstanding.
- Optional read-side: with an operator-supplied **management** key, query the analytics API (grouped by `app`,
  `model`, `provider`, `external_user`) and store the daily figures in an additive table `or_analytics_daily`
  (migration `0003`, same schema, same roles) next to our own totals, so Stage 5 can show "ours vs OpenRouter's"
  and discrepancies surface. Without the Stage 3 sink the comparison is computed from the local store instead.
- Gate: confirm Broadcast cost matches local cost for a week before anything relies on it.

### Stage 5 — Loop back into opsview + BI, reading from the cost store  (R8)

**Store selection.** Stage 5 reads through one `CostsSource` interface with two backends. **Local** (default) computes
everything in memory from the NDJSON files with the existing `summarize` code, so the console works with no database.
**Postgres** (when `OC_OBS_READ_POSTGRES_URL` is set) reads the Stage 3 views over a connection that uses the
**reader role only**. The read URL is separate from the shipper's writer URL on purpose: the console's database
identity is read-only and cannot see `call_content`. One call to `/api/costs` reads from exactly one backend, so
totals never mix sources.

- `/costs` page and `/api/costs` (same session + CSRF posture as existing routes): per-chat, per-run, per-goal,
  per-model, per-provider tables; time series; cap burn; top spenders; anomalies (cost-per-run outlier, retry
  storm, estimated usage); drill-down to the exact generations (and, on the installation that produced them, the log
  tail for that trace). Postgres backend queries `v_chat_cost`, `v_run_cost`, `v_model_cost_daily`, `v_unreconciled`
  and `v_cost_outliers`; the anomaly rules live in those views so BI tools see the same flags as the console. Logs are
  not copied to the database, so log drill-down works only where the logs live.
- **Database unreachable.** Reads use a short statement timeout and a circuit breaker (retry after a cool-down).
  While open, the page falls back to the local backend and shows a banner saying so and naming the data's age. A
  slow or dead database never hangs a page load and never changes what the cost cap sees.
- Per-chat cost chip on the goal timeline (reuse the event-stream `cost` event; add `generation_id`); the chip does
  not query the database.
- BI: `GET /api/costs/export.csv` (either backend). For richer analysis the runbook documents the reader role and
  the views, so any tool with a Postgres connector (a notebook, a SQL client, a dashboard tool the operator already
  runs) can query the same numbers; no BI tool is bundled or required.
- Alerts: console banner by default and an optional generic webhook (`OC_ALERT_WEBHOOK_URL`) for budget burn,
  evaluated by the console process from the same views, so alerts do not depend on any external alerting product.
- Tests: both backends return identical totals on one seeded dataset; the reader role cannot read content through
  any `/api/costs` path; with the database killed mid-request the page returns local data with the banner within the
  timeout; CSV export matches the on-screen table.

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
| Database unreachable or slow | The shipper is a separate process reading the local files; runs never call it; backlog drains in order on recovery; `sink status` shows lag; explicit blocked state for auth and schema errors |
| Telemetry rows readable by the wrong party in Supabase | Schema kept off the Data API, row-level security on every table, no `anon`/`authenticated` grants, dedicated non-bypass roles, views with `security_invoker`, tests for each |
| Prompt text lands in a remote database | Content is a separate table, shipped only when `OC_OBS_CAPTURE_CONTENT=1` at ship time, stripped at the boundary, 14-day default expiry, reader role cannot select it |
| Sink credential leaks | URL never logged or written to status, accepted from a file, redactor masks connection URLs, test with a seeded password |
| Rows lost before they ship | Local rotation is cursor-aware; a forced drop is an ERROR with a count; `sink status` flags gaps |
| Hosted-database storage cap or idle pause | Surfaces as unreachable or blocked; retention tunable; `cost_daily` keeps long-range totals after detail rows expire |
| Duplicate rows after crash or retry | Natural keys with `ON CONFLICT`, position advanced only after commit, replay test |
| OpenRouter response shape drifts | Parse defensively; unknown usage fields kept (redacted) in `raw_usage`; `cost_source: none` is visible |
| Generation API is eventually consistent | Reconcile with backoff and a pending state; never block a run on it |
| Broadcast sends content to a third party | Privacy Mode recommended for every shared destination; owner-only setup |
| Public app listing exposes usage by default | Documented; `hidden` visibility and `off` switch; default only shares token counts per model under the project's app, as OpenRouter's rankings do for any attributed app |
| Double counting when Broadcast data and the local store are both used | Stage 5 reads one source per request and labels it; Broadcast is never summed with the local store |
| Obs code slows or breaks a run | Failure isolation + a unit test that proves a raising sink does not fail `run_once` |

## Scope Boundaries

### Deferred to Follow-Up Work

- **OTLP traces/logs and Prometheus metrics export, with a sample Grafana stack** (the former Stage 3). Not
  scheduled. If revived it is a second, independent optional sink behind its own extra and `OTEL_*` variables,
  reading the same local files, and must not change the Postgres sink. The spans-nested-under-ours idea
  (`trace.parent_span_id`) only makes sense then.
- Periodic Parquet export. A Postgres-capable tool can read the store directly, so this adds little.
- Table partitioning, once real volume shows it is needed.
- Letting Supabase Auth users read rows over the Data API (the model is single-owner).
- A second, remote-only content switch (see Open Questions).

### Non-goals

- A bundled BI product or dashboard server.
- Moving the cost cap, ledger or audit log into the database.
- Any operator-specific host, project reference or credential in code, samples or docs.

## Open Questions

### Deferred to implementation

- Exact column types, index set and view SQL (S3-U1), once measured against realistic row counts.
- Supported Postgres floor. Planned: 15 and newer, because `security_invoker` views need it.
- Whether `sink run` should also be startable from the console process for single-box setups.

### Needs the maintainer

- Is one extra opt-in for sending content to a *remote* database wanted, in addition to `OC_OBS_CAPTURE_CONTENT=1`?
  (Planned: no, to match the stated rule exactly.)
- Are the retention defaults right: 90 days of call detail, 14 days of content, run and chat history kept?
- Is `oc_obs` the right schema name, and is one database shared by several installations (the `instance_id` label)
  a real need?
- Should the deferred OTLP/Prometheus export stay on the list at all?

## Sequencing

Stage 1 → Stage 2 need no operator decision and ship back to back (done). Stage 3 needs no further operator
decision: its destination is any Postgres the operator points it at (a separate hosted project now, the operator's
own server later; only the URL changes). Within Stage 3 the order is S3-U1, S3-U2 and S3-U3 (independent), then
S3-U4, then S3-U5 and S3-U6. Stage 4 needs decision 2 (plus owner action in the OpenRouter dashboard) and benefits
from Stage 3 but does not require it. Stage 5 starts on local data (the local backend) and gains the Postgres
backend once S3-U1 exists. Stage 6 is
independent and only starts when the maintainer schedules it, after Stage 1 (it needs the call store) and the
Stage 4 reconcile loop (it needs trustworthy request counts).
