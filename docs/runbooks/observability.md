# Observability and OpenRouter cost data (self-hoster guide)

Stage 1 of [the plan](../plans/2026-10-28-001-feat-observability-and-cost-telemetry-plan.md). Everything here is
configured with environment variables and works on a fresh checkout. Nothing is sent anywhere unless you opt in.

## What you get

- **Structured logs** (`oc.obs`): JSON lines with `trace_id`, `run_id`, `subtask`, `task`, `chat_id`, `service`,
  levels, and secrets/PII redacted at the sink.
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

In code: `from oc.obs import get_logger, bind` then `log = get_logger("router")`, `log.info("msg", key=value)`,
and `with bind(run_id=..., chat_id=...):` to attach ids. A logging failure never raises into a run.

**Redaction** masks values under secret-looking keys (`*key`, `token`, `secret`, `password`, `authorization`,
`cookie`, `dsn` ...), credential/PII shapes (API keys, bearer tokens, JWTs, GitHub/AWS/Slack tokens, `user:pass@`
URLs, emails, phone numbers), and the literal value of any environment variable whose name looks secret.
Prompt and completion text is **not** stored unless `OC_OBS_CAPTURE_CONTENT=1` (then still redacted).

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
(`tests/unit/fake_openrouter.py`) covers the client. Later stages (OTLP/Grafana export, Broadcast setup,
console cost views) are in the plan.
