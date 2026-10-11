# Goal event stream

The contract between the orchestrator (which produces events) and the agent workspace (which renders them).
Binding spec: Silver Lining section 16 (`docs/design/silver-lining.md`, docketry PR #107). This file owns the wire format;
the machine-readable form is [`oc/events/schema/event.schema.json`](../../oc/events/schema/event.schema.json).

**One stream per goal. The sidebar and the timeline are pure projections of it.** Reloading, resuming after a
disconnect and replaying a recording all rebuild exactly the same screen, because nothing the UI shows is derived
from its own clock or from anything outside the stream.

## Envelope

```json
{"id":"g_research01:12","v":1,"goal":"g_research01","seq":12,"ts":1791700021.4,"type":"source",
 "actor":{"name":"researcher","session":"sess-4f21"},"data":{ ... }}
```

| Field | Rules |
|---|---|
| `id` | `"<goal>:<seq>"`. Stable and unique. |
| `v` | Always `1`. A breaking change bumps it; readers must refuse an unknown `v`. |
| `goal` | `g_` + lowercase letters, digits, underscore. |
| `seq` | Integer, **gapless and strictly increasing per goal, starting at 1**. It is also the SSE `id`. Assigned by the log under a lock, never by the producer. |
| `ts` | Epoch seconds, millisecond precision. Non-decreasing. The UI computes every elapsed time and "ago" from event `ts`, never from `Date.now()`. |
| `type` | One of `run step tool_call source artifact change approval cost note error`. |
| `actor` | Optional `{name, session, kind}`. Drives the provenance glint (section 10): anything an agent created is attributed. |
| `data` | Per-type payload below. `additionalProperties: false` everywhere, so a typo fails validation instead of silently vanishing. |

## Upsert semantics

Events about a thing (a step, a tool call, a source, an approval...) carry its `id` in `data`. A later event with the
same `id` **merges over** the earlier one: fields present replace, fields absent are kept. That is how a step goes
`pending -> running -> done` and a tool call gains `output_tail` and `duration_ms`. Entities appear in the UI in the
order they were **first** seen and never move afterwards.

Entity ids: lowercase prefix, underscore, body (`step_fetch`, `tool_s1`, `ap_push01`).

## Event types

### `run`: the goal's lifecycle
`status` (`queued planning running waiting paused done failed cancelled`); `reason` when waiting or paused
(`approval plan_approval cap loop_guard user error`); optional `title`, `goal_text`, `summary`, `model`, `cap_usd`, and
`box {template ttl_s cpu mem_mb disk_mb network_allow[]}` (feeds the Box tab). **A run at its cap is `paused` with
`reason: "cap"`, never `cancelled`.** `done`, `failed` and `cancelled` are terminal; the SSE stream ends after one.

### `step`: the plan
`id`, `title`, `status` (`pending running done failed skipped`); optional `parent`, `kind` (`step subagent sandbox`),
`agent` (name of the sub-agent or sandbox on that row), `detail`. Only `running` steps shimmer; at most one at a time.
`subagent` and `sandbox` rows get **Stop** and **Send note**.

### `tool_call`
`id`, `name` (`web.search`, `web.fetch`, `shell.exec`, `fs.write`, `git.push`...), `status` (`running ok error`); optional
`step`, `summary`, `destination` (host or path), `output_tail[]` (last <= 50 lines; feeds the Box terminal),
`duration_ms`, `repeat` (consecutive identical calls so far; **the loop guard pauses at 3**), `cost_usd`.

### `source`
`id`, `n`, `url`, `title`; optional `domain`, `snippet`, `fetched`, `fetched_at`, `content_hash`, `cited_by[]`. `n` is the
citation number shown in chat, assigned once in order of first appearance. **`fetched: false` is a source cited from a
search snippet; the UI flags it "Not verified".** No favicon URL travels in the stream: the self-hosted UI draws a
letter tile and never fetches from a third party.

### `artifact`
`id`, `name`, `kind`, `version`, `url`; optional `mime`, `size`, `rows`, `language`, `step`, `summary`,
`export {requires_approval, approval}`. `kind` is one of `markdown html image svg pdf csv code text diff mermaid archive
office audio video`. `url` is a same-origin path (`/goals/<id>/artifacts/<aid>`); an external URL fails validation. A new
`version` marks the artifact unreviewed again. `rows` is the total for `csv`; the viewer shows the first 100.

### `change`
`id`, `path`, `op` (`add modify delete rename`), `additions`, `deletions`; optional `diff` (unified diff against the
sandbox's starting point) and `step`. This is what would leave the box.

### `approval`
`id`, `status` (`pending approved denied superseded expired`), `kind` (`network push email spend export plan`),
`action`, `payload_hash`, `risk` (`low elevated high`); optional `destination`, `payload`, `payload_preview`, `why`,
`cost_usd`, `if_denied`, `scope_pattern`, `supersedes`, `step`, and on resolution `decision` (`once goal deny`),
`decided_by`, `decided_at`, `note`.

Rules the emitter enforces and the server re-checks:

1. **`payload_hash` is `sha256` over the canonical JSON of `payload`** (`json.dumps(payload, sort_keys=True)`), the same
   function `services/gate/queue.py` uses. The producer never supplies it.
2. **An approval id is single-use.** Once resolved it cannot change state again.
3. **A changed payload is a new request**: new id with `supersedes: "<old id>"`. The old one becomes `superseded` in the
   stream *and* in the queue, so it can never be approved.
4. **`high` risk needs a second press.** The server answers `428` to an allow without `confirmed: true`.
5. `decision: "goal"` grants `scope_pattern` for the rest of the goal. The orchestrator treats later matching requests as
   pre-approved and emits them already `approved` with `decided_by: "policy:goal-grant"`, so Outbox still lists every one.
6. There is no "always allow".

### `cost`
Cumulative snapshot (idempotent): `spent_usd`, `cap_usd`; optional `tokens {in out cached}`, `by_model[]
{model in out cached usd}`, `context {used window}`. `threshold` (`0.8` or `1.0`) is set on the event that first crosses
80% or 100% of the cap; the emitter sets it, and a `note` of `kind: threshold` accompanies it. At `1.0` the producer
emits `run paused reason=cap`.

### `note`: the conversation
`id`, `role` (`agent user system`), `text` (Markdown); optional `kind` (`message final threshold status`), `cites[]
{n, quote}`, `to` (a sub-agent step, for Send note). Citations are written `[1]`; adjacent chips merge (`[1][2][3]`
renders `1-3`). File citations are `[path:line]` and open Changes at that line.

### `error`
`id`, `code`, `message`; optional `hint` (what to do next), `step`, `recoverable`. Copy follows section 12: what went
wrong and what to do, no apology.

## Transport: `GET /goals/<id>/events`

Server-sent events, same session and CSP as the console. No GET has a side effect: opening the stream never starts work.

```
retry: 2000

id: 12
event: source
data: {"id":"g_research01:12", ...}

: hb
```

* **Resume.** Send `Last-Event-ID: <seq>` (the browser's `EventSource` does this on reconnect) or `?last_event_id=<seq>`.
  The server replays every event with a higher `seq`, then tails. A reconnect never loses or duplicates events; clients
  also drop any `seq <= last seen`.
* **Reset.** If the client claims a `seq` the log does not have (a replay was restarted), the server sends `event: reset`
  and streams from 1. Clients clear their state.
* **Heartbeat.** `: hb` comment every 15 s (`?hb=<seconds>` to change it).
* **End.** After a terminal `run` status the server sends `event: end` and closes. Clients must `close()` the
  `EventSource` on `end` or the browser reconnects forever.
* **Sweep.** `GET /goals/<id>/approvals` returns the authoritative approval list. The UI calls it after every reconnect so a
  pending approval cannot be missed.

Other endpoints: `GET /goals.json` (goal list with the server-side fold), `GET /goals/<id>/state`, `GET
/goals/<id>/artifacts/<aid>` (+ `/listing` for archives), and the POSTs `POST /workspace/new`, `/goals/<id>/messages`, `/stop`,
`/resume`, `/approvals/<aid>/decide`, `/artifacts/<aid>/export`, `/steps/<sid>/stop`, `/replay`. Every POST needs the
session, a CSRF token and a same-origin `Origin`.

Artifact bytes are served with a per-type CSP. HTML gets `sandbox allow-scripts; default-src 'none'`, so it runs but
cannot reach the network or the console origin; the viewer embeds it in `<iframe sandbox="allow-scripts">` and the page's own
CSP has `frame-src 'self'`, which also stops the frame navigating away. Opening it in a new tab is a separate, explicit action.
Downloading an artifact whose `export.requires_approval` is true is refused (`403`) until an `export` approval for it is `approved`.

## Producing events: `oc.events`

```python
from oc.events import Emitter, FileLog

em = Emitter(FileLog("/var/lib/oc/events"), "g_research01", actor={"name": "researcher", "session": "sess-4f21"},
             approvals=approval_queue)            # optional: registers requests with services/gate/queue.py
em.run("running", title="Compare ...", cap_usd=2.0, box={...})
em.step("step_search", "Search for comparisons", "running")
em.tool_call("tool_s1", "web.search", "running", step="step_search", destination="html.duckduckgo.com")
em.source("src_a", 1, "https://...", "Title", domain="...", fetched=True)
em.approval_request("ap_push01", kind="push", action="git push origin fix", destination="github.com/acme/app",
                    payload={...}, risk="high", why="...", if_denied="...")
em.cost(0.81, 1.0, tokens={...})                  # threshold=0.8 is set for you
em.run("done", summary="...")
```

Every call validates before anything is written, so a bad event raises and leaves no gap in `seq`. `MemoryLog` is for
tests and replay; `FileLog` is one append-only NDJSON file per goal (`<dir>/<goal>.ndjson`), tolerant of a torn last line.
`oc.events.validate.validate(event)` and `errors(event)` check any event against the schema without a third-party library.

## Projection rules (what the UI may and may not do)

* The UI state is `fold(events)`. `static/projection.js` is that fold, `oc/events/summary.py` is the server's copy; a
  browser test asserts they agree on status, spend, cap, pending approvals and counts for every fixture.
* Apply events in batches, at most 4 a second. A burst of 200 events must not move scroll, the selected tab, focus or
  the row under the cursor; new timeline items wait behind an "N new" pill.
* Tabs never switch on their own. A pending approval turns the Outbox badge amber and docks the card without taking focus.

## Replay

`OC_REPLAY=1` (or `serve_fixture.py --replay`) serves the recordings in `services/workspace/fixtures/`: no sandbox, no
model, no network. `POST /goals/<id>/replay {"action":"start|restart","speed":4,"hold":true}`. `speed` divides recorded gaps
(capped at 1.5 s; `0` is instant). With `hold` on, replay pauses at each recorded approval and at the cap, and the human's
real decision (payload-hash bound, single-use, two presses on high risk) stands in for the recorded one; with `hold` off the
recording plays exactly as captured, which is what the determinism gate uses.

The three streams (research report, coding task with an approval to push, goal that hits the cap) are authored with the real
`Emitter` by `services/workspace/fixtures/build.py` and are byte-reproducible; a test regenerates and diffs them. They are
**scripted, not captured from a live run**, because no orchestrator emits events yet. When it does, record real runs into the
same format.

## Versioning

Adding an optional field or an enum value is compatible. Renaming or removing anything, tightening a type, or changing
`seq`/`ts` semantics bumps `v`. The schema is the source of truth; if this document and the schema disagree, fix this document.
