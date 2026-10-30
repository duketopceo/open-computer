# Observability and OpenRouter cost data (self-hoster guide)

Stages 1 and 2 of [the plan](../plans/2026-10-28-001-feat-observability-and-cost-telemetry-plan.md). Everything
here is configured with environment variables and works on a fresh checkout. Logs and records stay on your disk;
nothing is sent anywhere unless you opt in.

## What you get

- **Structured logs** (`oc.obs`): JSON lines with `trace_id`, `run_id`, `subtask`, `task`, `chat_id`, `service`,
  levels, and secrets/PII redacted at the sink. Every service uses the same format (see
  [What each service logs](#what-each-service-logs)).
- **One-command diagnosis**: `python -m oc.obs tail --run <id>` merges the logs, the orchestrator audit, the cost
  ledger, the OpenRouter call record and the event stream into one timeline.
- **A local record of every OpenRouter request** (`services.openrouter`): model requested/served, provider, tokens
  (in/out/cached/reasoning), provider-reported cost, latency, finish reason, status/errors, generation id, and the
  ids above. Append-only NDJSON you can query offline.
- **Real spend in the cost ledger.** Cloud workers and the planner report the provider's `usage.cost`, so the
  per-run cap reacts to actual dollars (previously a flat estimate was used and the ledger was not fed).
- **App attribution on every OpenRouter call**, so usage can be filtered by app in OpenRouter's Activity page.

## Logging

| Variable | Default | Effect |
|---|---|---|
| `OC_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `OC_DEBUG` | off | `1` = DEBUG everywhere |
| `OC_LOG_LEVEL_<SERVICE>` | unset | per-service level, e.g. `OC_LOG_LEVEL_ROUTER=DEBUG` |
| `OC_LOG_DIR` | unset | also write rotating `oc.log` (NDJSON) there |
| `OC_LOG_MAX_BYTES` / `OC_LOG_BACKUPS` | 20 MB / 5 | rotation |
| `OC_LOG_STDERR` | `1` | `0` silences stderr |

Log lines carry **metadata only**: ids, counts, byte sizes, categories, statuses, durations. Prompt text,
completion text, tool-call argument values and exception messages (which can echo any of those) are left out unless
you set `OC_OBS_CAPTURE_CONTENT=1`; they are still redacted when you do. The full text of an error is also in the
audit rows and the call record on your disk, which is where `oc.obs tail` reads it from.

| Variable | Default | Effect |
|---|---|---|
| `OC_OBS_CAPTURE_CONTENT` | off | `1` = also log prompt/argument/error text (redacted) and store prompts and completions in the call record |

In code: `from oc.obs import get_logger, bind` then `log = get_logger("router")`, `log.info("msg", key=value)`,
and `with bind(run_id=..., chat_id=...):` to attach ids. A logging failure never raises into a run.

**Redaction** masks values under secret-looking keys (`*key`, `token`, `secret`, `password`, `authorization`,
`cookie`, `dsn` ...), credential/PII shapes (API keys, bearer tokens, JWTs, GitHub/AWS/Slack tokens, `user:pass@`
URLs, emails, phone numbers), and the literal value of any environment variable whose name looks secret.
Prompt and completion text is **not** stored unless `OC_OBS_CAPTURE_CONTENT=1` (then still redacted).

## What each service logs

The `service` field names the component that wrote the line. `INFO` is the normal story of a run; `WARNING` is
something an operator should look at; `DEBUG` (set `OC_DEBUG=1`, or `OC_LOG_LEVEL_<SERVICE>=DEBUG` for one
service) adds the inputs behind a decision. Nothing at any level contains scanned text, secrets or file paths.

| `service` | INFO | WARNING / ERROR | DEBUG adds |
|---|---|---|---|
| `orchestrator` | run submitted (subtask count, planner cost), run recovered, subtask dispatched, run settled | dependency failed (dependent tombstoned), run failed before dispatch (ERROR), subtask parked | the plan's shape (ids, deps, tiers) |
| `worker` | task claimed, task done (ms) | task held over the cost cap, task failed (ERROR) | |
| `router` | route decided (route, tier, engine, confidence, cost, ms) | engine down and which one, every engine down (table fallback), budget halt | input length |
| `placement` | placement decided (target, reason, headroom) | placement blocked | inputs summary, local endpoint probes |
| `gate` | approval requested / granted / denied / decided, cloud call allowed | blocked, and replayed or forged approvals | scan categories (never the text), bytes sent |
| `cost` | cost recorded (run, model, tokens, cost, spend, cap), cap set, run resumed | run paused at the cap, ledger unreadable (ERROR) | reserve / release |
| `execplane` | sandbox create attempt / created, exec (command *length*, return code, ms), close, policy change applied | create refused or failed, agent-proposed policy refused, backend unreachable | backend probes |
| `device` | register, pairing, grants added or revoked, parked task drained | heartbeat gap, device marked offline, envelope refused (reason only), grant denied, device killed | heartbeats, envelope sealed and verified, grant used |
| `device_relay` | relay request (route name, status, ms, device id) | 4xx and 5xx responses, including unauthorized | polling routes (heartbeat, inbox) |
| `device_agent` | agent started, task claimed, step executed (kind, ms) | step refused (reason), relay unreachable, result write deferred | |
| `mcp` | tool call (name, argument *shape*, status, ms) | failed or malformed call | argument values, only with `OC_OBS_CAPTURE_CONTENT=1` |
| `opsview` | one access line per request (below), console start, passkey login and enrollment | 4xx and 5xx, with `reason`: `unauthenticated`, `csrf`, `cross_origin`, `body_too_large`, `passkey_denied` | |
| `openrouter` | call (model, provider, tokens, cost, ms, generation id) | failed call, call with no reported cost | request sent |
| `telemetry` | | a send to a configured error sink failed (sink name and error class; never the URL or key) | send ok |

### The console's request id and access log

Every console request gets an id. A well-formed inbound `X-Request-Id` (8-64 characters of `A-Z a-z 0-9 . _ -`)
is kept so a reverse proxy's id carries through; anything else is replaced by a generated one. The id is returned
in the `X-Request-Id` response header and is on every log line the request produces. The access line has `method`,
`route` (the handler name such as `g_run`, not the raw path, so ids never show up as separate routes), `status`,
`ms`, `actor` and, for rejections, `reason`. `actor` is `local` for a loopback session and otherwise a short hash
(`u_1a2b3c4d`), so you can tell users apart without an email address in the log. Query strings, cookies, headers
and bodies are never logged.

## Diagnose a failed run from one trace id

A run's trace id is `sha256("oc-run:<run id>")` truncated to 32 hex characters, so it is the same after a restart
and on another process. Every log line, call record and OpenRouter `trace` object for the run carries it.

1. Start with the run id from the console, or any log line with `"level":"ERROR"` (`run_id` and `trace_id` are
   on it).
2. Print the timeline. Set the same environment the services ran with (`OC_LOG_DIR`, `OC_ROOT`, `OC_COST_LEDGER`,
   `OC_OBS_DIR`, `OC_EVENTS_DIR`), or pass `--log-dir`, `--audit`, `--ledger`:

   ```sh
   python -m oc.obs tail --run <run-id>                 # or --trace <32-hex> / --goal <goal-id>
   python -m oc.obs tail --run <run-id> --level WARNING # only what needs attention
   python -m oc.obs tail --trace <32-hex> --json | jq . # one JSON object per row
   python -m oc.obs tail --run <run-id> --sources       # which files were read, and which are missing
   ```

   Rows from the five sources are interleaved by time: `log` (structured logs), `audit` (dispatch and worker
   rows), `ledger` (cost), `calls` (OpenRouter requests) and `events` (the goal event stream). A run found by its
   id shows the same rows as one found by its trace id, even for sources that only record the run id.
3. Read it top to bottom:
   - `run submitted` then `placement decided` shows where each subtask was sent and why.
   - `cost recorded` and `run paused: cap` show real spend against the cap. A held task (`task held`) is waiting
     for you to resume the run in the console, not failed.
   - `audit worker_fail` carries the full error text; the matching `task failed` log line carries the error class.
   - A `calls` row with `status: error` has the HTTP status and a redacted error. A `calls` row without a cost
     (`cost_source: none`) is visible in the OpenRouter report too.
4. If logs are missing, the run happened without `OC_LOG_DIR` and only stderr kept them. The audit, ledger and call
   record are always written, so `tail` still shows those.

`tail` redacts again on the way out, and never prints the `prompt` or `completion` fields of a call record, even
if you captured them.

## OpenRouter call record and reports

Records live in `$OC_OBS_DIR/openrouter-calls.ndjson` (default `~/.local/share/oc/obs`).

```sh
python -m services.openrouter report --by chat        # chat | run | subtask | model | provider | kind | day | status
python -m services.openrouter report --by run --chat <chat-id> --json
OPENROUTER_API_KEY=... python -m services.openrouter reconcile   # compare with OpenRouter's generation stats
```

`reconcile` re-reads each generation from `GET /api/v1/generation` and marks it `reconciled`, `mismatch` (cost or
token counts differ) or `missing` (OpenRouter does not have it yet; retried next time). Run it from cron or a
timer whenever you like; it never blocks a run.

`chat_id`: pass `chat_id=` to `Orchestrator.submit`; it defaults to the run id. It becomes the OpenRouter
`session_id`, so a conversation's generations group together there as well.

## App attribution and per-request tags

Sent on every OpenRouter request (planner, workers, sandbox script writer, router decisions, bench scripts).

| Variable | Default | Effect |
|---|---|---|
| `OC_OPENROUTER_ATTRIBUTION` | `on` | `off` = no attribution headers and no `user` field |
| `OC_OPENROUTER_REFERER` | this project's public URL | `HTTP-Referer`: your app's identity |
| `OC_OPENROUTER_TITLE` | `Open Computer` | `X-OpenRouter-Title` (and `X-Title`) |
| `OC_OPENROUTER_CATEGORIES` | unset | up to 2 of OpenRouter's documented categories |
| `OC_OPENROUTER_APP_VISIBILITY` | unset | `hidden` = a *newly created* app stays out of public rankings |
| `OC_OPENROUTER_USER` | unset | opaque value, sent only as a truncated sha256 in `user` |
| `OC_OPENROUTER_ENVIRONMENT` | unset | `trace.environment` |
| `OC_OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | point at a proxy or test double |

What this enables in OpenRouter (verified against their docs on 2026-10-28):

- With `HTTP-Referer` (and a title) your usage becomes an **app**: filter and group by app in the Activity page and
  in the analytics API (`POST /api/v1/analytics/query`, needs a *management* key, filter by numeric app id).
  Public apps appear in OpenRouter's rankings; set the visibility variable (or your own referer) if you do not
  want that. `hidden` only applies when the app is first created.
- `session_id` (our chat id) and `user` are visible per generation and in analytics (`external_user`).
- The `trace` object (`trace_id`, `trace_name`, `span_name`, `generation_name`, `environment`, plus `oc_run`,
  `oc_subtask`, `oc_chat`) is passed to every **Broadcast** destination you configure in the OpenRouter dashboard.
  Those keys are not dimensions in OpenRouter's own Activity page; use your destination's query language, or the
  local call record, to slice by run or chat.

Because the default referer is the project's URL, every self-hoster who keeps it contributes to one shared app
entry in OpenRouter's analytics for this project. Set your own referer or turn attribution off if you prefer.

## Tests and safety

The test suite runs with no ambient `OPENROUTER_API_KEY` and a throwaway `OC_OBS_DIR`; a fake OpenRouter server
(`tests/unit/fake_openrouter.py`) covers the client, and a suite-wide guard (`tests/conftest.py`) fails any test
that tries to open a connection to the real OpenRouter. Other guard rails that run in CI:

- redaction: seeded keys, tokens and emails never appear in any log or the call record, across a full run;
- metadata only: goal text, plan prompts, completions and error text never appear in logs unless
  `OC_OBS_CAPTURE_CONTENT=1`;
- no bare `print()` in `services/` or `oc/` (command-line tools whose output is their interface are listed in
  `tests/unit/test_obs_lint.py`), and every service must log through `oc.obs`;
- no operator-specific hostnames, domains or paths in observability code or docs.

Later stages (an optional Postgres/Supabase sink for the call and cost data, Broadcast setup, console cost views) are in the plan.
